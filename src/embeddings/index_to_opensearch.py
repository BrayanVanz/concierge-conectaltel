"""Ingests generated embeddings into AWS OpenSearch Serverless separate indices per chunk strategy."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import List

from vector_store import OpenSearchVectorStore, OpenSearchVectorStoreError, load_embedded_jsonl

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("index_to_opensearch")


def _iter_embedded_files(input_path: str) -> List[str]:
    path = Path(input_path)
    if path.is_file():
        return [str(path)]
    if path.is_dir():
        return sorted(str(p) for p in path.glob("*_embedded.jsonl"))
    raise FileNotFoundError(f"Arquivo ou diretório não encontrado: {input_path}")


def _derive_index_name(base_index: str, file_path: str) -> str:
    """
    Deriva o nome do índice com base na estratégia no nome do arquivo.
    Exemplo: chunks_fixed_window_embedded.jsonl -> concierge-vectors-fixed-windows
    """
    file_name = Path(file_path).name.lower()

    if "fixed_window" in file_name:
        strategy = "fixed-windows"
    elif "full_document" in file_name:
        strategy = "full-document"
    elif "hierarchical" in file_name:
        strategy = "hierarchical-semantic"
    else:
        strategy = "default"

    return f"{base_index}-{strategy}"


def _index_file(endpoint: str, region: str, base_index_name: str, file_path: str) -> int:
    docs = load_embedded_jsonl(file_path)
    if not docs:
        logger.warning("Arquivo sem embeddings para indexar | file=%s", file_path)
        return 0

    target_index = _derive_index_name(base_index_name, file_path)

    store = OpenSearchVectorStore(
        endpoint=endpoint,
        region=region,
        index_name=target_index,
    )

    dimension = len(docs[0]["embedding"])
    store.ensure_index(dimension)
    response = store.bulk_index(docs)

    logger.info(
        "Índices enviados | index=%s | file=%s | total=%d | ok=%s",
        target_index,
        file_path,
        len(docs),
        response.get("items") is not None,
    )
    return len(docs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa embeddings em índices separados no OpenSearch Serverless")
    parser.add_argument("--input", required=True, help="Caminho para arquivo *_embedded.jsonl ou diretório com vários")
    parser.add_argument("--endpoint", default=os.environ.get("OPENSEARCH_ENDPOINT") or os.environ.get("OPENSEARCH_COLLECTION_ENDPOINT"), help="Endpoint HTTPS do OpenSearch Serverless")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="Região AWS")
    parser.add_argument("--index-name", default=os.environ.get("OPENSEARCH_INDEX_NAME", "concierge-vectors"), help="Prefixo do índice")
    args = parser.parse_args()

    if not args.endpoint:
        logger.error("OPENSEARCH_ENDPOINT obrigatório ou passe a flag --endpoint.")
        return 2

    total = 0
    try:
        for file_path in _iter_embedded_files(args.input):
            total += _index_file(args.endpoint, args.region, args.index_name, file_path)
    except (FileNotFoundError, OpenSearchVectorStoreError) as exc:
        logger.exception("Falha ao indexar embeddings | motivo=%s", exc)
        return 1

    logger.info("Finalizado | total_documentos_indexados=%s", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
