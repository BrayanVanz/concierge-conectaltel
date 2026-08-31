import argparse
import json
from pathlib import Path

from src.vector_index.indexer import VectorIndex


def main() -> int:
    parser = argparse.ArgumentParser(description="Pesquisa vetorial em embeddings JSONL.")
    parser.add_argument("--file", required=True, help="Caminho do arquivo JSONL com embeddings.")
    parser.add_argument("--query", required=True, help="Consulta em texto livre para embedding; o projeto ainda não gera embedding em tempo real aqui.")
    parser.add_argument("--top-k", type=int, default=5, help="Número de resultados a retornar.")
    parser.add_argument("--status", default="vigente", help="Filtra por status, por exemplo: vigente")
    args = parser.parse_args()

    path = Path(args.file)
    index = VectorIndex.from_jsonl(path)

    print(f"Índice carregado: {path}")
    print(f"Modo de uso: este script espera uma query vetorial já calculada. Para a busca real, use um embedding do texto via Bedrock e chame VectorIndex.search([...], top_k={args.top_k}, status_filter='{args.status}').")
    print("Exemplo de uso em código:")
    print("  index = VectorIndex.from_jsonl('data/embeddings/chunks_hierarchical_semantic_embedded.jsonl')")
    print("  hits = index.search(query_vector, top_k=5, status_filter='vigente')")
    print(json.dumps({"query": args.query, "status_filter": args.status, "top_k": args.top_k}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
