"""Ingests generated embeddings into an AWS OpenSearch Serverless index."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from pathlib import Path
from typing import Iterable, List

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


def _index_file(store: OpenSearchVectorStore, file_path: str) -> int:
    docs = load_embedded_jsonl(file_path)
    if not docs:
        logger.warning("Arquivo sem embeddings para indexar | file=%s", file_path)
        return 0

    dimension = len(docs[0]["embedding"])
    store.ensure_index(dimension)
    response = store.bulk_index(docs)
    logger.info(
        "Índices enviados | file=%s | total=%d | ok=%s",
        file_path,
        len(docs),
        response.get("items") is not None,
    )
    return len(docs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa embeddings em OpenSearch Serverless")
    parser.add_argument("--input", required=True, help="Caminho para um arquivo *_embedded.jsonl ou diretório com vários arquivos")
    parser.add_argument("--endpoint", default=os.environ.get("OPENSEARCH_ENDPOINT") or os.environ.get("OPENSEARCH_COLLECTION_ENDPOINT"), help="Endpoint HTTPS do OpenSearch Serverless")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="Região AWS do domínio")
    parser.add_argument("--index-name", default=os.environ.get("OPENSEARCH_INDEX_NAME", "concierge-vectors"), help="Nome do índice vetorial")
    args = parser.parse_args()

    if not args.endpoint:
        logger.error("OPENSEARCH_ENDPOINT/OPENSEARCH_COLLECTION_ENDPOINT obrigatório ou --endpoint.")
        return 2

    store = OpenSearchVectorStore(
        endpoint=args.endpoint,
        region=args.region,
        index_name=args.index_name,
    )

    total = 0
    try:
        for file_path in _iter_embedded_files(args.input):
            total += _index_file(store, file_path)
    except (FileNotFoundError, OpenSearchVectorStoreError) as exc:
        logger.exception("Falha ao indexar embeddings | motivo=%s", exc)
        return 1

    logger.info("Finalizado | total_documentos_indexados=%s", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
