"""
Módulo Embedder do pipeline de RAG do Concierge ConectaTel.

O que este módulo faz, em uma frase: ele pega os chunks de texto gerados
pela etapa de chunking, transforma cada um em um vetor de embedding usando
o Amazon Bedrock (modelo Cohere Embed v4) e salva o resultado — os campos
originais do chunk mais o vetor — em um novo arquivo JSONL.

Onde ele fica no pipeline:

    ingestion.py         Markdown              -> corpus.jsonl
    chunk_strategies.py  corpus.jsonl          -> data/chunks/chunks_*.jsonl
    embedder.py (aqui)   data/chunks/*.jsonl   -> data/embeddings/*_embedded.jsonl

A entrada vem da pasta `data/chunks/` (onde a etapa de chunking grava) e a
saída vai para `data/embeddings/` — uma pasta só para os arquivos já
vetorizados, irmã de `data/chunks/`. O módulo só usa a rede para uma coisa:
chamar o Bedrock e gerar os vetores. Ele não lê nem escreve em S3.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("embedder")


# ---------------------------------------------------------------------------
# Constantes de configuração
# ---------------------------------------------------------------------------

# Modelo fixo, decisão de arquitetura já validada para este desafio.
# Não deve haver fallback para outro modelo nem sobrescrita via variável de
# ambiente: o corpus é 100% em português e pequeno (12 documentos), e o
# Cohere Embed v4 é o único, dentre os avaliados no Bedrock, que declara
# suporte explícito ao idioma português (o Titan Text Embeddings V2 só
# oferece suporte multilíngue em "Preview"). Ver README.md para o
# comparativo completo de modelos.
EMBEDDING_MODEL_ID = "cohere.embed-v4:0"

# Dimensão de saída do Cohere Embed v4 no Bedrock (valor fixo do modelo).
EMBEDDING_DIMENSIONS = 1536

# Cohere Embed v4 aceita até 96 textos por chamada de API. Processar em
# lotes reduz drasticamente o número de chamadas de rede em comparação a
# uma chamada por chunk, o que diminui custo e tempo total de execução.
MAX_BATCH_SIZE = 96

# Número de tentativas em caso de erro transitório (throttling, timeout de
# rede, erro 5xx do serviço). Erros de entrada inválida (4xx que não sejam
# throttling) não são re-tentados, pois tentar de novo não resolveria.
MAX_RETRY_ATTEMPTS = 4

# Backoff exponencial entre tentativas, em segundos: 1s, 2s, 4s, 8s...
RETRY_BASE_DELAY_SECONDS = 1.0

# Códigos de erro do Bedrock/boto3 que indicam falha transitória e
# justificam nova tentativa.
RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHUNKS_DIR = _REPO_ROOT / "data" / "chunks"
DEFAULT_EMBEDDINGS_DIR = _REPO_ROOT / "data" / "embeddings"
_DEFAULT_INPUT_PATH = DEFAULT_CHUNKS_DIR / "chunks_hierarchical_semantic.jsonl"


def embedded_output_path(input_path: Path) -> Path:
    """
    Devolve o caminho do arquivo de saída para um arquivo de chunks de
    entrada, seguindo uma convenção única:

        data/chunks/chunks_fixed_window.jsonl
        ->  data/embeddings/chunks_fixed_window_embedded.jsonl

    Ou seja: os arquivos vetorizados vão sempre para a pasta
    `data/embeddings/` (irmã de `data/chunks/`), mantendo o nome do arquivo
    de origem e ganhando o sufixo "_embedded". Ter isso em uma função só
    evita que a regra apareça duplicada (e acabe divergindo) entre a
    configuração e o main.py.
    """
    input_path = Path(input_path)
    return DEFAULT_EMBEDDINGS_DIR / f"{input_path.stem}_embedded{input_path.suffix}"


def find_chunk_files(directory: Path = DEFAULT_CHUNKS_DIR) -> List[Path]:
    """
    Lista, em ordem alfabética, todos os arquivos `.jsonl` de chunks de um
    diretório, ignorando os que terminam em "_embedded".

    É o que o main.py usa quando você não aponta um arquivo específico: nesse
    caso ele processa o diretório inteiro, um embedding por arquivo.
    """
    directory = Path(directory)
    return sorted(
        path
        for path in directory.glob("*.jsonl")
        if not path.stem.endswith("_embedded")
    )


class EmbedderError(Exception):
    """
    Exceção base para todos os erros previstos deste módulo.

    Usar uma exceção dedicada (em vez de deixar propagar exceções genéricas
    do boto3/json) permite que quem chama este módulo (main.py, benchmarks,
    ou uma futura Lambda) capture especificamente falhas do Embedder sem
    precisar conhecer detalhes internos de boto3 ou do parser de JSON.
    """


class InvalidChunkError(EmbedderError):
    """Levantada quando um registro do JSONL de entrada não tem os campos mínimos exigidos."""


class EmbeddingGenerationError(EmbedderError):
    """Levantada quando a chamada ao Bedrock falha de forma definitiva (sem mais tentativas)."""


class ChunkStorageError(EmbedderError):
    """Levantada quando a leitura do arquivo de entrada ou a escrita do arquivo de saída falha."""


@dataclass(frozen=True)
class EmbedderConfig:
    """
    Configuração imutável de uma execução do Embedder.

    Usar uma dataclass "frozen" (imutável) evita que algum bug modifique a
    configuração no meio da execução — qualquer tentativa de reatribuir um
    campo depois de criado levanta erro em tempo de execução, o que torna
    bugs desse tipo visíveis imediatamente em vez de causarem comportamento
    inconsistente silencioso.

    Attributes:
        input_path: caminho local do arquivo JSONL de chunks de entrada.
        output_path: caminho local onde o JSONL enriquecido com os vetores
            será gravado.
        aws_region: região AWS onde o Bedrock será invocado.
        model_id: identificador do modelo de embedding no Bedrock (fixo, ver
            EMBEDDING_MODEL_ID).
        batch_size: quantidade de textos enviados por chamada ao Bedrock.
    """

    input_path: Path = _DEFAULT_INPUT_PATH
    output_path: Optional[Path] = None
    aws_region: str = "us-east-1"
    model_id: str = EMBEDDING_MODEL_ID
    batch_size: int = MAX_BATCH_SIZE

    def __post_init__(self) -> None:
        """
        Roda automaticamente logo depois que o objeto é criado. Serve para
        duas coisas:

        1. Normalizar `input_path`/`output_path` para `Path` (aceitando
           string) e, se `output_path` não for informado, derivá-lo do
           `input_path` pela convenção "_embedded". Como a dataclass é
           "frozen", o jeito de escrever um campo durante a construção é
           `object.__setattr__`.
        2. Validar a configuração de uma vez. Preferimos falhar aqui, com
           uma mensagem clara, a falhar minutos depois no meio de um lote,
           com um erro difícil de rastrear até a causa raiz.

        Quando o `Embedder` roda como Lambda (ver lambda_function.py) ele só
        usa `aws_region`/`model_id`/`batch_size` e chama `embed_chunks()`
        diretamente — os caminhos ficam nos valores padrão e nunca são lidos.
        """
        object.__setattr__(self, "input_path", Path(self.input_path))
        if self.output_path is None:
            object.__setattr__(self, "output_path", embedded_output_path(self.input_path))
        else:
            object.__setattr__(self, "output_path", Path(self.output_path))

        if not str(self.input_path).strip():
            raise ValueError("input_path não pode ser vazio.")
        if not str(self.output_path).strip():
            raise ValueError("output_path não pode ser vazio.")
        if self.model_id != EMBEDDING_MODEL_ID:
            # Trava de segurança: mesmo que alguém tente instanciar a
            # configuração com outro model_id, isso é rejeitado aqui.
            # A decisão de usar apenas o Cohere Embed v4 é intencional
            # e documentada no README — não deve haver fallback silencioso.
            raise ValueError(
                f"model_id deve ser '{EMBEDDING_MODEL_ID}' (decisão fixa do projeto), "
                f"recebido: '{self.model_id}'."
            )
        if not (1 <= self.batch_size <= MAX_BATCH_SIZE):
            raise ValueError(
                f"batch_size deve estar entre 1 e {MAX_BATCH_SIZE}, recebido: {self.batch_size}."
            )

    @staticmethod
    def from_environment() -> "EmbedderConfig":
        """
        Monta a configuração lendo variáveis de ambiente, com valores padrão
        que já apontam para as pastas locais do projeto.

        A ideia é que você consiga rodar o módulo sem configurar nada: os
        padrões leem o arquivo da estratégia hierárquica em `data/chunks/` e
        gravam o resultado em `data/embeddings/`. Se precisar de outro
        arquivo — outra estratégia de chunking, por exemplo — basta definir
        as variáveis abaixo antes de executar.

        Variáveis lidas (todas opcionais):
            EMBEDDINGS_INPUT_PATH   caminho do JSONL de chunks de entrada.
                Padrão: data/chunks/chunks_hierarchical_semantic.jsonl
            EMBEDDINGS_OUTPUT_PATH  caminho do JSONL de saída, já com os
                vetores. Padrão: data/embeddings/ + nome do arquivo de
                entrada + sufixo "_embedded" (ver embedded_output_path).
            AWS_REGION              região usada para chamar o Bedrock.
                Padrão: us-east-1

        Returns:
            EmbedderConfig já validado.
        """
        input_path = Path(
            os.environ.get("EMBEDDINGS_INPUT_PATH", str(_DEFAULT_INPUT_PATH))
        )
        output_path = Path(
            os.environ.get("EMBEDDINGS_OUTPUT_PATH", str(embedded_output_path(input_path)))
        )

        return EmbedderConfig(
            input_path=input_path,
            output_path=output_path,
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        )


@dataclass
class EmbeddingRunStats:
    """
    Estatísticas coletadas durante uma execução do Embedder.

    Mantidas separadas da lógica de negócio para que possam ser inspecionadas
    por quem chama (por exemplo, os scripts de benchmark) sem precisar
    reprocessar nada.
    """

    total_chunks_read: int = 0
    total_chunks_embedded: int = 0
    total_chunks_skipped_invalid: int = 0
    total_batches_sent: int = 0
    total_retries: int = 0
    elapsed_seconds: float = 0.0
    batch_latencies_seconds: List[float] = field(default_factory=list)


class Embedder:
    """
    Gera embeddings para chunks de texto usando Amazon Bedrock (Cohere Embed v4)
    e grava o resultado enriquecido em um arquivo JSONL local.

    A classe concentra toda a lógica: leitura do JSONL de entrada, validação
    de cada registro, chamada em lote ao Bedrock com retentativa automática,
    e escrita do JSONL de saída. Os scripts main.py e de benchmark apenas
    instanciam esta classe e chamam seus métodos públicos — nenhuma lógica
    de negócio deve viver fora daqui.
    """

    def __init__(self, config: EmbedderConfig) -> None:
        """
        Args:
            config: configuração já validada da execução (ver EmbedderConfig).
        """
        self._config = config
        self._stats = EmbeddingRunStats()

        logger.info(
            "Inicializando Embedder | modelo=%s | região=%s | entrada=%s | motivo=início de execução",
            self._config.model_id,
            self._config.aws_region,
            self._config.input_path,
        )

        self._bedrock_client = boto3.client(
            "bedrock-runtime", region_name=self._config.aws_region
        )

    @property
    def stats(self) -> EmbeddingRunStats:
        """Estatísticas da execução mais recente (ou em andamento)."""
        return self._stats

    # -----------------------------------------------------------------
    # Orquestração principal
    # -----------------------------------------------------------------

    def run(self) -> EmbeddingRunStats:
        """
        Executa o pipeline completo: lê os chunks do arquivo local, gera os
        embeddings em lote via Bedrock e grava o resultado em outro arquivo
        local.

        Returns:
            EmbeddingRunStats com as métricas da execução.

        Raises:
            ChunkStorageError: se a leitura do arquivo de entrada ou a
                escrita do arquivo de saída falhar.
            EmbeddingGenerationError: se o Bedrock falhar de forma definitiva
                (após esgotar as tentativas) para algum lote.
        """
        start_time = time.monotonic()

        chunks = self._read_chunks_from_file()
        embedded_records = self.embed_chunks(chunks)
        self._write_records_to_file(embedded_records)

        self._stats.elapsed_seconds = time.monotonic() - start_time

        logger.info(
            "Execução concluída | chunks_lidos=%d | chunks_embedados=%d | "
            "chunks_invalidos_ignorados=%d | lotes_enviados=%d | retentativas=%d | "
            "tempo_total_segundos=%.2f | motivo=fim de execução",
            self._stats.total_chunks_read,
            self._stats.total_chunks_embedded,
            self._stats.total_chunks_skipped_invalid,
            self._stats.total_batches_sent,
            self._stats.total_retries,
            self._stats.elapsed_seconds,
        )

        return self._stats

    def embed_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Valida e gera embeddings para uma lista de chunks **já em memória**,
        sem nenhum I/O de arquivo ou S3.

        É o núcleo reutilizável do módulo: quem lê e escreve os chunks fica
        de fora daqui. `run()` (execução local, lê/grava arquivo) e o
        `lambda_function.py` (execução na AWS, lê/grava S3) chamam este
        mesmo método no meio.

        Args:
            chunks: lista de dicionários, um por chunk (formato de saída da
                etapa de chunking).

        Returns:
            Lista de dicionários: cada chunk válido acrescido de `embedding`
            e `embedding_model`. Chunks inválidos são registrados em log e
            descartados (não entram no resultado).

        Raises:
            EmbeddingGenerationError: se o Bedrock falhar de forma definitiva
                para algum lote.
        """
        self._stats.total_chunks_read = len(chunks)
        valid_chunks = self._validate_chunks(chunks)
        embedded_records = self._embed_chunks(valid_chunks)
        self._stats.total_chunks_embedded = len(embedded_records)
        return embedded_records

    # -----------------------------------------------------------------
    # Leitura do arquivo local
    # -----------------------------------------------------------------

    def _read_chunks_from_file(self) -> List[Dict[str, Any]]:
        """
        Lê o arquivo JSONL de chunks do disco e devolve uma lista de dicionários.

        Sobre o formato JSONL: cada linha do arquivo é um objeto JSON
        completo e independente. Isso traz uma vantagem prática — se uma
        única linha estiver corrompida, ela é registrada em log e pulada,
        sem derrubar a leitura das demais. Linhas em branco (comuns no fim
        do arquivo) são simplesmente ignoradas.

        Returns:
            Uma lista com um dicionário por chunk lido com sucesso.

        Raises:
            ChunkStorageError: se o arquivo não existir, não puder ser lido,
                ou não estiver codificado em UTF-8.
        """
        path = self._config.input_path
        logger.info(
            "Lendo chunks de entrada | caminho=%s | motivo=início da leitura", path
        )

        try:
            raw_content = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            logger.error(
                "Arquivo de chunks não encontrado | caminho=%s | motivo=%s", path, exc
            )
            raise ChunkStorageError(
                f"Arquivo de chunks não encontrado: '{path}'. Rode a etapa de "
                f"chunking antes, ou aponte a variável EMBEDDINGS_INPUT_PATH para "
                f"o arquivo correto."
            ) from exc
        except OSError as exc:
            logger.error(
                "Falha ao ler o arquivo de chunks | caminho=%s | motivo=%s", path, exc
            )
            raise ChunkStorageError(
                f"Não foi possível ler '{path}'. Verifique as permissões de "
                f"leitura do arquivo."
            ) from exc
        except UnicodeDecodeError as exc:
            logger.error(
                "Arquivo de chunks não está em UTF-8 | caminho=%s | motivo=%s", path, exc
            )
            raise ChunkStorageError(
                f"O conteúdo de '{path}' não é UTF-8 válido. Verifique a "
                f"codificação do arquivo gerado pela etapa de chunking."
            ) from exc

        chunks: List[Dict[str, Any]] = []
        malformed_line_count = 0

        for line_number, line in enumerate(raw_content.splitlines(), start=1):
            stripped_line = line.strip()
            if not stripped_line:
                continue
            try:
                chunks.append(json.loads(stripped_line))
            except json.JSONDecodeError as exc:
                malformed_line_count += 1
                logger.warning(
                    "Linha malformada ignorada no JSONL de entrada | "
                    "caminho=%s | linha=%d | motivo=%s",
                    path,
                    line_number,
                    exc,
                )

        if malformed_line_count > 0:
            logger.warning(
                "Total de linhas malformadas ignoradas | caminho=%s | quantidade=%d | "
                "motivo=json inválido em cada linha reportada acima",
                path,
                malformed_line_count,
            )

        logger.info(
            "Leitura de entrada concluída | caminho=%s | chunks_lidos=%d | motivo=fim da leitura",
            path,
            len(chunks),
        )

        return chunks

    # -----------------------------------------------------------------
    # Validação
    # -----------------------------------------------------------------

    def _validate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filtra os chunks lidos, mantendo apenas os que têm a estrutura mínima
        necessária para gerar um embedding com metadados rastreáveis.

        Um chunk válido precisa ter, no mínimo: 'chunk_id' (para rastrear a
        origem do vetor) e 'text_content' não vazio (o texto a ser embedado).
        Chunks sem esses campos são registrados em log com o motivo específico
        e excluídos do processamento — eles não geram embedding e não entram
        no arquivo de saída, mas também não interrompem a execução dos demais.

        Args:
            chunks: lista de dicionários lidos do JSONL de entrada.

        Returns:
            Lista apenas com os chunks que passaram na validação.
        """
        valid_chunks: List[Dict[str, Any]] = []

        for index, chunk in enumerate(chunks):
            try:
                self._assert_valid_chunk(chunk)
                valid_chunks.append(chunk)
            except InvalidChunkError as exc:
                self._stats.total_chunks_skipped_invalid += 1
                chunk_id = chunk.get("chunk_id", "<sem chunk_id>") if isinstance(chunk, dict) else "<registro não é um objeto>"
                logger.warning(
                    "Chunk inválido ignorado | índice=%d | chunk_id=%s | motivo=%s",
                    index,
                    chunk_id,
                    str(exc),
                )

        logger.info(
            "Validação concluída | total_lido=%d | válidos=%d | inválidos_ignorados=%d | "
            "motivo=filtragem antes do envio ao Bedrock",
            len(chunks),
            len(valid_chunks),
            self._stats.total_chunks_skipped_invalid,
        )

        return valid_chunks

    @staticmethod
    def _assert_valid_chunk(chunk: Any) -> None:
        """
        Verifica se um único registro tem a estrutura mínima esperada.

        Raises:
            InvalidChunkError: com uma mensagem específica do problema encontrado.
        """
        if not isinstance(chunk, dict):
            raise InvalidChunkError(
                f"esperado um objeto JSON (dict), recebido tipo '{type(chunk).__name__}'."
            )

        chunk_id = chunk.get("chunk_id")
        if not chunk_id or not isinstance(chunk_id, str):
            raise InvalidChunkError("campo 'chunk_id' ausente, vazio ou não é texto.")

        text_content = chunk.get("text_content")
        if not text_content or not isinstance(text_content, str) or not text_content.strip():
            raise InvalidChunkError("campo 'text_content' ausente, vazio ou não é texto.")

    # -----------------------------------------------------------------
    # Geração de embeddings
    # -----------------------------------------------------------------

    def _embed_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Gera embeddings para todos os chunks válidos, processando em lotes.

        Args:
            chunks: chunks já validados por _validate_chunks.

        Returns:
            Lista de dicionários: cada chunk original acrescido do campo
            'embedding' (lista de floats) e 'embedding_model' (identificador
            do modelo usado, para rastreabilidade futura caso o modelo mude).

        Raises:
            EmbeddingGenerationError: se algum lote falhar definitivamente
                após esgotar as tentativas de retentativa.
        """
        if not chunks:
            logger.warning(
                "Nenhum chunk válido para gerar embeddings | motivo=lista de "
                "entrada vazia após validação"
            )
            return []

        embedded_records: List[Dict[str, Any]] = []
        batches = self._split_into_batches(chunks, self._config.batch_size)

        logger.info(
            "Iniciando geração de embeddings | total_chunks=%d | total_lotes=%d | "
            "tamanho_lote=%d | motivo=início do processamento em lote",
            len(chunks),
            len(batches),
            self._config.batch_size,
        )

        for batch_index, batch in enumerate(batches, start=1):
            texts = [chunk["text_content"] for chunk in batch]

            batch_start = time.monotonic()
            vectors = self._invoke_bedrock_with_retry(texts, batch_index, len(batches))
            batch_elapsed = time.monotonic() - batch_start

            self._stats.total_batches_sent += 1
            self._stats.batch_latencies_seconds.append(batch_elapsed)

            if len(vectors) != len(batch):
                # Defesa contra resposta inconsistente do modelo: se o
                # provedor devolver uma quantidade de vetores diferente da
                # quantidade de textos enviados, não há como saber com
                # segurança qual vetor pertence a qual chunk. Preferimos
                # falhar ruidosamente a gravar dados incorretos silenciosamente.
                raise EmbeddingGenerationError(
                    f"Lote {batch_index}/{len(batches)}: Bedrock retornou "
                    f"{len(vectors)} vetores para {len(batch)} textos enviados. "
                    f"Abortando para evitar associação incorreta entre chunk e vetor."
                )

            for chunk, vector in zip(batch, vectors):
                enriched = dict(chunk)
                enriched["embedding"] = vector
                enriched["embedding_model"] = self._config.model_id
                embedded_records.append(enriched)

            logger.info(
                "Lote processado | lote=%d/%d | chunks_no_lote=%d | "
                "tempo_segundos=%.2f | motivo=progresso do processamento",
                batch_index,
                len(batches),
                len(batch),
                batch_elapsed,
            )

        return embedded_records

    @staticmethod
    def _split_into_batches(
        items: List[Dict[str, Any]], batch_size: int
    ) -> List[List[Dict[str, Any]]]:
        """Divide uma lista em sublistas de no máximo `batch_size` itens."""
        return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

    def _invoke_bedrock_with_retry(
        self, texts: List[str], batch_index: int, total_batches: int
    ) -> List[List[float]]:
        """
        Invoca o Bedrock para um lote de textos, com retentativa automática
        em caso de erro transitório (throttling, indisponibilidade momentânea).

        Args:
            texts: lista de textos a serem embedados neste lote.
            batch_index: número do lote atual, apenas para logging.
            total_batches: total de lotes, apenas para logging.

        Returns:
            Lista de vetores de embedding, na mesma ordem dos textos de entrada.

        Raises:
            EmbeddingGenerationError: se todas as tentativas se esgotarem.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                return self._call_bedrock_embed(texts)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")
                last_exception = exc

                if error_code not in RETRYABLE_ERROR_CODES:
                    # Erro não-transitório (ex: entrada inválida, modelo não
                    # encontrado, acesso negado). Tentar de novo não ajudaria,
                    # então falhamos imediatamente com uma mensagem clara.
                    logger.error(
                        "Erro não recuperável do Bedrock | lote=%d/%d | código=%s | motivo=%s",
                        batch_index,
                        total_batches,
                        error_code,
                        str(exc),
                    )
                    raise EmbeddingGenerationError(
                        f"Lote {batch_index}/{total_batches}: erro não recuperável "
                        f"do Bedrock (código '{error_code}'): {exc}"
                    ) from exc

                if attempt >= MAX_RETRY_ATTEMPTS:
                    # Última tentativa esgotada: não há próxima espera, então
                    # apenas registramos a falha e deixamos o laço terminar
                    # naturalmente (a exceção final é levantada após o loop).
                    logger.warning(
                        "Erro transitório do Bedrock na última tentativa disponível | "
                        "lote=%d/%d | tentativa=%d/%d | código=%s | motivo=%s",
                        batch_index,
                        total_batches,
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        error_code,
                        str(exc),
                    )
                    break

                self._stats.total_retries += 1
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Erro transitório do Bedrock, tentando novamente | lote=%d/%d | "
                    "tentativa=%d/%d | código=%s | espera_segundos=%.1f | motivo=%s",
                    batch_index,
                    total_batches,
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                    error_code,
                    delay,
                    str(exc),
                )
                time.sleep(delay)
            except BotoCoreError as exc:
                # Erros de rede/transporte (timeout, conexão recusada etc)
                # também são tratados como transitórios e re-tentados.
                last_exception = exc

                if attempt >= MAX_RETRY_ATTEMPTS:
                    logger.warning(
                        "Erro de transporte ao chamar Bedrock na última tentativa disponível | "
                        "lote=%d/%d | tentativa=%d/%d | motivo=%s",
                        batch_index,
                        total_batches,
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        str(exc),
                    )
                    break

                self._stats.total_retries += 1
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Erro de transporte ao chamar Bedrock, tentando novamente | "
                    "lote=%d/%d | tentativa=%d/%d | espera_segundos=%.1f | motivo=%s",
                    batch_index,
                    total_batches,
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                    delay,
                    str(exc),
                )
                time.sleep(delay)

        logger.error(
            "Lote falhou definitivamente após esgotar tentativas | lote=%d/%d | "
            "tentativas=%d | motivo=%s",
            batch_index,
            total_batches,
            MAX_RETRY_ATTEMPTS,
            str(last_exception),
        )
        raise EmbeddingGenerationError(
            f"Lote {batch_index}/{total_batches}: falha definitiva após "
            f"{MAX_RETRY_ATTEMPTS} tentativas. Último erro: {last_exception}"
        ) from last_exception

    def _call_bedrock_embed(self, texts: List[str]) -> List[List[float]]:
        """
        Faz uma única chamada ao Bedrock InvokeModel para o Cohere Embed v4.

        Não faz retentativa — essa responsabilidade é de _invoke_bedrock_with_retry.
        Este método apenas monta a requisição, invoca e parseia a resposta.

        Args:
            texts: lista de textos a embedar (até MAX_BATCH_SIZE itens).

        Returns:
            Lista de vetores de embedding (lista de floats), na mesma ordem
            dos textos de entrada.

        Raises:
            ClientError: erros reportados pelo próprio serviço Bedrock (4xx/5xx).
            BotoCoreError: erros de transporte/rede.
            EmbeddingGenerationError: se a resposta do Bedrock vier em um
                formato inesperado (contrato de API mudou ou resposta corrompida).
        """
        request_body = {
            "texts": texts,
            "input_type": "search_document",
            "embedding_types": ["float"],
        }

        response = self._bedrock_client.invoke_model(
            modelId=self._config.model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )

        stream = response.get("body") or response.get("Body")
        if stream is None:
            raise EmbeddingGenerationError(
                "Resposta do Bedrock não trouxe o corpo esperado (chave 'body'). "
                f"Chaves recebidas: {sorted(response)}"
            )

        try:
            response_body = json.loads(stream.read())
        except json.JSONDecodeError as exc:
            raise EmbeddingGenerationError(
                "Resposta do Bedrock não é um JSON válido."
            ) from exc

        try:
            vectors = response_body["embeddings"]["float"]
        except (KeyError, TypeError) as exc:
            raise EmbeddingGenerationError(
                "Formato de resposta do Bedrock inesperado: campo "
                "'embeddings.float' não encontrado. A API do modelo pode ter "
                f"mudado. Corpo recebido (truncado): {str(response_body)[:300]}"
            ) from exc

        return vectors

    # -----------------------------------------------------------------
    # Escrita do arquivo local
    # -----------------------------------------------------------------

    def _write_records_to_file(self, records: List[Dict[str, Any]]) -> None:
        """
        Grava os chunks já enriquecidos (com 'embedding' e 'embedding_model')
        em um novo arquivo JSONL, no caminho definido em `output_path`.

        O arquivo é montado inteiro em memória e escrito de uma vez só. Como
        o corpus do desafio é pequeno (dezenas de chunks), isso é mais
        simples e mais seguro do que escrever linha a linha: ou o arquivo
        final sai completo, ou não sai — você nunca fica com um arquivo pela
        metade se algo falhar no meio.

        Args:
            records: lista de chunks já com os campos de embedding adicionados.

        Raises:
            ChunkStorageError: se o diretório de destino não puder ser criado
                ou o arquivo não puder ser escrito.
        """
        path = self._config.output_path

        if not records:
            logger.warning(
                "Nenhum registro para gravar | caminho=%s | motivo=lista de "
                "embeddings vazia, nada será escrito",
                path,
            )
            return

        logger.info(
            "Gravando resultado | caminho=%s | total_registros=%d | motivo=início da escrita",
            path,
            len(records),
        )

        jsonl_lines = (json.dumps(record, ensure_ascii=False) for record in records)
        jsonl_content = "\n".join(jsonl_lines) + "\n"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(jsonl_content, encoding="utf-8")
        except OSError as exc:
            logger.error(
                "Falha ao gravar o arquivo de saída | caminho=%s | motivo=%s", path, exc
            )
            raise ChunkStorageError(
                f"Não foi possível gravar '{path}'. Verifique as permissões de "
                f"escrita no diretório de destino."
            ) from exc

        logger.info(
            "Escrita concluída com sucesso | caminho=%s | total_registros=%d | motivo=fim da escrita",
            path,
            len(records),
        )
