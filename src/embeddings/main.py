"""
Ponto de entrada da etapa de geração de embeddings do pipeline de RAG do
Concierge ConectaTel.

Este script só faz a "amarração": decide QUAIS arquivos processar, monta uma
configuração para cada um e chama o Embedder. Toda a lógica de negócio
(leitura, validação, chamada em lote ao Bedrock, retry, escrita) vive em
`embedder.py` — este arquivo é propositalmente fino para o fluxo de
orquestração ser fácil de auditar de relance.

O que ele processa:

    - Se a variável EMBEDDINGS_INPUT_PATH estiver definida, processa apenas
      aquele arquivo (e respeita EMBEDDINGS_OUTPUT_PATH, se também estiver
      definida). Útil para testar uma única estratégia de chunking.
    - Caso contrário, processa TODOS os arquivos .jsonl da pasta
      `data/chunks/` — gerando um arquivo por entrada em `data/embeddings/`,
      com o nome derivado automaticamente (sufixo "_embedded").

Uso:

        python src/embeddings/main.py

Códigos de saída:
        0  todos os arquivos foram processados com sucesso
        1  falha de configuração, ou nenhum arquivo de chunks encontrado
        2  falha durante a execução de algum arquivo (erro de arquivo ou do Bedrock)
"""

import logging
import os
import sys
from typing import List

try:
    # python-dotenv é opcional: em produção (Lambda) as variáveis já vêm do
    # ambiente. Localmente, `pip install python-dotenv` habilita o carregamento
    # automático de um arquivo .env na raiz do projeto.
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

from embedder import (
    Embedder,
    EmbedderConfig,
    EmbedderError,
    embedded_output_path,
    find_chunk_files,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("main")


def build_configs() -> List[EmbedderConfig]:
    """
    Monta a lista de configurações a executar, seguindo a regra descrita no
    topo do arquivo:

    - EMBEDDINGS_INPUT_PATH definida  -> uma configuração, aquele arquivo.
    - EMBEDDINGS_INPUT_PATH ausente   -> uma configuração por arquivo .jsonl
      encontrado em data/chunks/.

    Returns:
        Lista de EmbedderConfig já validados. Pode vir vazia se o modo
        "pasta inteira" não encontrar nenhum arquivo — quem chama trata
        esse caso.

    Raises:
        EmbedderError / ValueError: se alguma configuração for inválida
        (propagado de EmbedderConfig).
    """
    if os.environ.get("EMBEDDINGS_INPUT_PATH"):
        # Modo arquivo único: from_environment já resolve entrada, saída e região.
        return [EmbedderConfig.from_environment()]

    # Modo pasta inteira: uma configuração por arquivo, com a saída derivada
    # pela mesma convenção de nome usada no modo arquivo único.
    region = os.environ.get("AWS_REGION", "us-east-1")
    return [
        EmbedderConfig(
            input_path=path,
            output_path=embedded_output_path(path),
            aws_region=region,
        )
        for path in find_chunk_files()
    ]


def main() -> int:
    """
    Decide o que processar, roda o Embedder para cada arquivo e devolve um
    código de saída apropriado para linha de comando ou automação.

    Returns:
        Código de saída do processo (0 = sucesso, != 0 = falha).
    """
    try:
        configs = build_configs()
    except (EmbedderError, ValueError) as exc:
        # Erro de configuração: nada foi executado ainda. Falha rápida com
        # mensagem clara.
        logger.error(
            "Não foi possível montar a configuração do Embedder | motivo=%s",
            str(exc),
        )
        return 1

    if not configs:
        logger.error(
            "Nenhum arquivo de chunks encontrado em data/chunks/ | "
            "motivo=rode a etapa de chunking antes, ou defina EMBEDDINGS_INPUT_PATH"
        )
        return 1

    total = len(configs)
    logger.info("Arquivos a processar | quantidade=%d | motivo=início da orquestração", total)

    for index, config in enumerate(configs, start=1):
        logger.info(
            "Processando arquivo %d/%d | entrada=%s | saída=%s",
            index,
            total,
            config.input_path,
            config.output_path,
        )
        try:
            Embedder(config).run()
        except EmbedderError as exc:
            # Falha prevista do domínio (ChunkStorageError,
            # EmbeddingGenerationError, InvalidChunkError) já foi logada com
            # detalhes dentro de embedder.py. Aqui paramos tudo: se um
            # arquivo falhou, é melhor você corrigir a causa e rodar de novo
            # do que seguir gerando saídas parciais.
            logger.error(
                "Execução interrompida no arquivo %s | motivo=%s",
                config.input_path,
                str(exc),
            )
            return 2
        except Exception as exc:  # noqa: BLE001 - barreira final intencional
            # Barreira contra qualquer exceção não prevista (bug não mapeado,
            # erro de biblioteca externa). Logamos com o stack trace completo
            # (logger.exception) antes de encerrar.
            logger.exception(
                "Erro inesperado no arquivo %s | motivo=%s",
                config.input_path,
                str(exc),
            )
            return 2

    logger.info(
        "Orquestração concluída | arquivos_processados=%d | motivo=fim de execução",
        total,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
