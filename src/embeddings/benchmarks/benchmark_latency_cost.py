"""
Benchmark de latência e custo para o Concierge ConectaTel.

Objetivo:
    Medir, em uma execução real contra o Amazon Bedrock, quanto tempo o
    Embedder leva para vetorizar o corpus completo e quanto essa execução
    custaria em dólares, com base no preço público por token de entrada do
    modelo Cohere Embed v4.

O que este script faz:
    1. Lê o arquivo de chunks (saída da etapa de chunking, ainda SEM
       embedding) diretamente do disco local.
    2. Estima a contagem de tokens de cada chunk (ver seção "Sobre a
       estimativa de tokens" abaixo).
    3. Invoca o Bedrock em lotes, exatamente como o embedder.py faz,
       medindo o tempo de cada lote e o tempo total.
    4. Calcula o custo estimado em USD com base no preço público do
       modelo e imprime um relatório com latência (total, média por lote,
       p50/p95) e custo estimado.

Sobre a estimativa de tokens:
    O Bedrock não expõe uma API de tokenização pública separada para o
    Cohere Embed v4, e instalar o tokenizer oficial da Cohere apenas para
    uma estimativa de custo seria uma dependência pesada para um script de
    benchmark. Em vez disso, este script usa uma aproximação amplamente
    usada para textos em português/inglês: 1 token ≈ 4 caracteres. Essa
    aproximação é conservadora o suficiente para dar uma ordem de grandeza
    confiável de custo, mas NÃO deve ser tratada como valor exato de
    cobrança — para o valor exato, seria necessário consultar o campo de
    uso de tokens retornado pela própria API (quando disponível) ou o
    Cost Explorer da AWS após a execução real.

Sobre o preço usado:
    Cohere Embed v4 no Amazon Bedrock (região us-east-1, sob demanda):
    USD 0,12 por 1.000.000 de tokens de entrada; não há cobrança de saída
    para embeddings. Preço público, verificado em múltiplas fontes
    (documentação da AWS e comparadores de preço de terceiros) em
    2026-06/07. Preços da AWS podem mudar — confirme o valor vigente em
    https://aws.amazon.com/bedrock/pricing/ antes de decisões orçamentárias.
"""

import argparse
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("benchmark_latency_cost")


# ---------------------------------------------------------------------------
# Configuração do benchmark
# ---------------------------------------------------------------------------

EMBEDDING_MODEL_ID = "cohere.embed-v4:0"
MAX_BATCH_SIZE = 96

# Caminhos padrão, derivados da posição deste arquivo
# (benchmarks/ -> embeddings/ -> src/ -> raiz do repositório).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CHUNKS_PATH = _REPO_ROOT / "data" / "chunks" / "chunks_hierarchical_semantic.jsonl"
_DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Aproximação de caracteres por token, ver docstring do módulo.
APPROX_CHARS_PER_TOKEN = 4.0

# Preço público do Cohere Embed v4 no Bedrock, ver docstring do módulo.
USD_PER_MILLION_INPUT_TOKENS = 0.12
PRICE_SOURCE_NOTE = (
    "USD 0.12 por 1.000.000 de tokens de entrada (Cohere Embed v4, Amazon "
    "Bedrock, sob demanda, região us-east-1). Verificado em múltiplas "
    "fontes públicas em 2026-06/07. Confirme o valor vigente em "
    "https://aws.amazon.com/bedrock/pricing/ antes de decisões orçamentárias."
)


class BenchmarkError(Exception):
    """Exceção base para falhas previstas na execução deste benchmark."""


@dataclass
class LatencyCostReport:
    """Relatório consolidado de latência e custo de uma execução."""

    total_chunks: int
    total_batches: int
    batch_size: int
    total_estimated_tokens: int
    total_elapsed_seconds: float
    batch_latencies_seconds: List[float] = field(default_factory=list)
    estimated_cost_usd: float = 0.0

    @property
    def mean_batch_latency_seconds(self) -> float:
        return statistics.mean(self.batch_latencies_seconds) if self.batch_latencies_seconds else 0.0

    @property
    def p50_batch_latency_seconds(self) -> float:
        return statistics.median(self.batch_latencies_seconds) if self.batch_latencies_seconds else 0.0

    @property
    def p95_batch_latency_seconds(self) -> float:
        if not self.batch_latencies_seconds:
            return 0.0
        sorted_latencies = sorted(self.batch_latencies_seconds)
        index = min(int(len(sorted_latencies) * 0.95), len(sorted_latencies) - 1)
        return sorted_latencies[index]

    @property
    def mean_seconds_per_chunk(self) -> float:
        return self.total_elapsed_seconds / self.total_chunks if self.total_chunks else 0.0


def parse_args() -> argparse.Namespace:
    """Define e interpreta os argumentos de linha de comando do benchmark."""
    parser = argparse.ArgumentParser(
        description=(
            "Mede latência e estima custo em USD de uma execução completa "
            "do Embedder contra um arquivo de chunks real."
        )
    )
    parser.add_argument(
        "--chunks-path",
        type=str,
        default=str(_DEFAULT_CHUNKS_PATH),
        help=(
            "Caminho local para o arquivo JSONL de chunks (saída da etapa de "
            "chunking, sem necessidade de já ter o campo 'embedding'). "
            "Padrão: data/chunks/chunks_hierarchical_semantic.jsonl."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_DEFAULT_RESULTS_DIR),
        help="Diretório onde o relatório JSON será salvo (padrão: benchmarks/results, ao lado deste script).",
    )
    parser.add_argument(
        "--aws-region",
        type=str,
        default="us-east-1",
        help="Região AWS usada para invocar o Bedrock (padrão: us-east-1).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_BATCH_SIZE,
        help=f"Quantidade de textos por chamada ao Bedrock (padrão e máximo: {MAX_BATCH_SIZE}).",
    )
    return parser.parse_args()


def load_chunks(path: Path) -> List[Dict[str, Any]]:
    """
    Lê o arquivo JSONL local de chunks (com ou sem embedding já calculado —
    este benchmark ignora qualquer embedding pré-existente e sempre chama
    o Bedrock de novo, pois o objetivo é medir a chamada em si).

    Args:
        path: caminho local do arquivo de chunks.

    Returns:
        Lista de dicionários, cada um contendo pelo menos 'text_content'.

    Raises:
        BenchmarkError: se o arquivo não existir, não puder ser lido, ou
            nenhum chunk válido for encontrado.
    """
    if not path.exists():
        raise BenchmarkError(
            f"Arquivo de chunks não encontrado: '{path}'. Informe o caminho "
            f"correto via --chunks-path (por exemplo, a saída do "
            f"chunk_strategies.py ou do process_chunks.py do módulo de chunking)."
        )

    logger.info(
        "Carregando chunks | caminho=%s | motivo=início da leitura do corpus de teste",
        path,
    )

    chunks: List[Dict[str, Any]] = []
    malformed_lines = 0
    missing_text_lines = 0

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkError(f"Não foi possível ler o arquivo '{path}': {exc}") from exc

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            malformed_lines += 1
            logger.warning("Linha malformada ignorada | linha=%d | motivo=%s", line_number, str(exc))
            continue

        if not isinstance(record, dict) or not record.get("text_content"):
            missing_text_lines += 1
            logger.warning(
                "Registro sem 'text_content' válido ignorado | linha=%d | motivo=campo ausente ou vazio",
                line_number,
            )
            continue

        chunks.append(record)

    if not chunks:
        raise BenchmarkError(
            f"Nenhum chunk com 'text_content' válido encontrado em '{path}'."
        )

    logger.info(
        "Chunks carregados | total_válidos=%d | linhas_malformadas=%d | "
        "linhas_sem_texto=%d | motivo=fim da leitura do corpus de teste",
        len(chunks),
        malformed_lines,
        missing_text_lines,
    )

    return chunks


def estimate_token_count(text: str) -> int:
    """
    Estima a quantidade de tokens de um texto usando a aproximação de
    caracteres por token (ver docstring do módulo para limitações).

    Args:
        text: texto a estimar.

    Returns:
        Número inteiro estimado de tokens (arredondado para cima, nunca
        menor que 1 para texto não vazio).
    """
    if not text:
        return 0
    return max(1, int(len(text) / APPROX_CHARS_PER_TOKEN) + 1)


def split_into_batches(items: List[Dict[str, Any]], batch_size: int) -> List[List[Dict[str, Any]]]:
    """Divide uma lista em sublistas de no máximo `batch_size` itens."""
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def call_bedrock_embed(bedrock_client: Any, texts: List[str]) -> None:
    """
    Faz uma única chamada ao Bedrock para embedar um lote de textos.

    Esta função descarta o resultado propositalmente — o objetivo deste
    benchmark é medir tempo e estimar custo, não persistir os vetores
    (essa é a responsabilidade do embedder.py). Ainda assim, valida que a
    resposta veio em formato reconhecível, para que uma mudança silenciosa
    de contrato de API não passe despercebida em uma medição de latência.

    Args:
        bedrock_client: cliente boto3 para 'bedrock-runtime'.
        texts: lista de textos do lote atual.

    Raises:
        BenchmarkError: se a chamada falhar ou a resposta vier em formato inesperado.
    """
    request_body = {
        "texts": texts,
        "input_type": "search_document",
        "embedding_types": ["float"],
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
    except (ClientError, BotoCoreError) as exc:
        raise BenchmarkError(f"Falha ao chamar o Bedrock: {exc}") from exc

    try:
        # 'bedrock-runtime' devolve o corpo na chave "body" (minúsculo).
        response_body = json.loads((response.get("body") or response["Body"]).read())
        vectors = response_body["embeddings"]["float"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BenchmarkError(f"Resposta do Bedrock em formato inesperado: {exc}") from exc

    if len(vectors) != len(texts):
        raise BenchmarkError(
            f"Bedrock retornou {len(vectors)} vetores para {len(texts)} textos enviados."
        )


def run_benchmark(chunks_path: Path, aws_region: str, batch_size: int) -> LatencyCostReport:
    """
    Executa o benchmark completo de latência e custo.

    Args:
        chunks_path: caminho local do arquivo JSONL de chunks.
        aws_region: região AWS para o cliente Bedrock.
        batch_size: quantidade de textos por chamada.

    Returns:
        LatencyCostReport com as métricas coletadas.

    Raises:
        BenchmarkError: se qualquer etapa falhar de forma não recuperável.
    """
    if not (1 <= batch_size <= MAX_BATCH_SIZE):
        raise BenchmarkError(
            f"batch_size deve estar entre 1 e {MAX_BATCH_SIZE}, recebido: {batch_size}."
        )

    chunks = load_chunks(chunks_path)
    total_estimated_tokens = sum(estimate_token_count(chunk["text_content"]) for chunk in chunks)

    logger.info(
        "Estimativa de tokens do corpus | total_chunks=%d | total_tokens_estimados=%d | "
        "motivo=base para o cálculo de custo (aproximação de %.0f caracteres por token)",
        len(chunks),
        total_estimated_tokens,
        APPROX_CHARS_PER_TOKEN,
    )

    logger.info(
        "Inicializando cliente Bedrock | região=%s | motivo=início da medição de latência",
        aws_region,
    )
    bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region)

    batches = split_into_batches(chunks, batch_size)
    batch_latencies: List[float] = []

    logger.info(
        "Iniciando medição | total_chunks=%d | total_lotes=%d | tamanho_lote=%d | "
        "motivo=início da execução cronometrada",
        len(chunks),
        len(batches),
        batch_size,
    )

    overall_start = time.monotonic()

    for batch_index, batch in enumerate(batches, start=1):
        texts = [chunk["text_content"] for chunk in batch]

        batch_start = time.monotonic()
        call_bedrock_embed(bedrock_client, texts)
        batch_elapsed = time.monotonic() - batch_start

        batch_latencies.append(batch_elapsed)

        logger.info(
            "Lote medido | lote=%d/%d | chunks_no_lote=%d | tempo_segundos=%.3f | "
            "motivo=progresso da medição",
            batch_index,
            len(batches),
            len(batch),
            batch_elapsed,
        )

    total_elapsed = time.monotonic() - overall_start

    estimated_cost_usd = (total_estimated_tokens / 1_000_000) * USD_PER_MILLION_INPUT_TOKENS

    report = LatencyCostReport(
        total_chunks=len(chunks),
        total_batches=len(batches),
        batch_size=batch_size,
        total_estimated_tokens=total_estimated_tokens,
        total_elapsed_seconds=total_elapsed,
        batch_latencies_seconds=batch_latencies,
        estimated_cost_usd=estimated_cost_usd,
    )

    logger.info(
        "Benchmark concluído | tempo_total_segundos=%.2f | latencia_media_lote_segundos=%.3f | "
        "latencia_p50_segundos=%.3f | latencia_p95_segundos=%.3f | "
        "custo_estimado_usd=%.6f | motivo=resumo final",
        report.total_elapsed_seconds,
        report.mean_batch_latency_seconds,
        report.p50_batch_latency_seconds,
        report.p95_batch_latency_seconds,
        report.estimated_cost_usd,
    )

    return report


def report_to_serializable_dict(report: LatencyCostReport) -> Dict[str, Any]:
    """Converte o relatório em um dicionário puro, pronto para json.dumps."""
    return {
        "total_chunks": report.total_chunks,
        "total_batches": report.total_batches,
        "batch_size": report.batch_size,
        "total_estimated_tokens": report.total_estimated_tokens,
        "token_estimation_method": (
            f"aproximação de {APPROX_CHARS_PER_TOKEN:.0f} caracteres por token "
            f"(não é contagem exata do tokenizer da Cohere)"
        ),
        "latency": {
            "total_elapsed_seconds": round(report.total_elapsed_seconds, 3),
            "mean_seconds_per_chunk": round(report.mean_seconds_per_chunk, 4),
            "mean_batch_latency_seconds": round(report.mean_batch_latency_seconds, 3),
            "p50_batch_latency_seconds": round(report.p50_batch_latency_seconds, 3),
            "p95_batch_latency_seconds": round(report.p95_batch_latency_seconds, 3),
        },
        "cost": {
            "estimated_cost_usd": round(report.estimated_cost_usd, 6),
            "price_source": PRICE_SOURCE_NOTE,
        },
    }


def save_report(report: LatencyCostReport, output_dir: Path) -> Path:
    """
    Salva o relatório em JSON no diretório de saída informado.

    Args:
        report: relatório consolidado da execução.
        output_dir: diretório local de destino (criado se não existir).

    Returns:
        Caminho completo do arquivo gravado.

    Raises:
        BenchmarkError: se o diretório não puder ser criado ou o arquivo
            não puder ser escrito.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BenchmarkError(
            f"Não foi possível criar o diretório de saída '{output_dir}': {exc}"
        ) from exc

    output_path = output_dir / "latency_cost_result.json"

    try:
        output_path.write_text(
            json.dumps(report_to_serializable_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise BenchmarkError(
            f"Não foi possível escrever o relatório em '{output_path}': {exc}"
        ) from exc

    logger.info(
        "Relatório salvo | caminho=%s | motivo=persistência do resultado do benchmark",
        output_path,
    )

    return output_path


def main() -> int:
    """Ponto de entrada de linha de comando deste benchmark."""
    args = parse_args()

    try:
        report = run_benchmark(
            chunks_path=Path(args.chunks_path),
            aws_region=args.aws_region,
            batch_size=args.batch_size,
        )
        save_report(report, Path(args.output_dir))
    except BenchmarkError as exc:
        logger.error("Benchmark interrompido | motivo=%s", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - barreira final intencional
        logger.exception("Erro inesperado no benchmark | motivo=%s", str(exc))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())