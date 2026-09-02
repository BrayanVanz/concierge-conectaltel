"""
Runner para benchmark de recuperação de versão vigente.

Executa o retrieval em duas condições:
- WITHOUT_VERSION_FILTER: busca sem filtro de status
- WITH_VERSION_FILTER: busca com filtro status='vigente'
"""
import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

# Adiciona a raiz do projeto ao PATH
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from src.vector_index.indexer import VectorIndex
from .evaluator import evaluate_query, calculate_summary, format_results

# Configurações
DEFAULT_EMBEDDINGS_PATH = Path(__file__).parent.parent.parent.parent / "data" / "embeddings" / "chunks_hierarchical_semantic_embedded.jsonl"
DEFAULT_QUESTIONS_PATH = Path(__file__).parent / "questions.json"
TOP_K_VALUES = [1, 3, 5]

def load_questions(questions_path: Path) -> List[Dict[str, Any]]:
    """Carrega as perguntas do arquivo JSON."""
    with open(questions_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def run_retrieval(
    index: VectorIndex,
    query_text: str,
    top_k: int,
    status_filter: str = None,
    aws_region: str = "us-east-1"
) -> List[Dict[str, Any]]:
    """
    Executa o retrieval usando o VectorIndex com geração de embedding real via Bedrock.
    
    Args:
        index: VectorIndex carregado
        query_text: Texto da consulta
        top_k: Número de resultados
        status_filter: Filtro de status (None = sem filtro, 'vigente' = com filtro)
        aws_region: Região AWS para Bedrock
    
    Returns:
        Lista de resultados com metadados
    """
    import boto3
    
    # Gerar embedding da query via Bedrock
    bedrock_client = boto3.client('bedrock-runtime', region_name=aws_region)
    
    body = json.dumps({
        "texts": [query_text],
        "input_type": "search_query",
        "embedding_types": ["float"]
    })
    
    response = bedrock_client.invoke_model(
        modelId="cohere.embed-v4:0",
        body=body
    )
    
    response_body = json.loads(response['body'].read())
    embeddings_data = response_body.get('embeddings')
    
    if isinstance(embeddings_data, dict) and 'float' in embeddings_data:
        query_vector = embeddings_data['float'][0]
    else:
        query_vector = embeddings_data[0]
    
    # Executar busca no índice
    results = index.search(
        query_vector=query_vector,
        top_k=top_k,
        status_filter=status_filter
    )
    
    return results


def run_benchmark(
    embeddings_path: Path,
    questions_path: Path,
    use_version_filter: bool,
    aws_region: str = "us-east-1"
) -> Dict[str, Any]:
    """
    Executa o benchmark completo.
    
    Args:
        embeddings_path: Caminho para o arquivo de embeddings
        questions_path: Caminho para o arquivo de perguntas
        use_version_filter: Se True, aplica filtro status='vigente'
        aws_region: Região AWS para Bedrock
    
    Returns:
        Dicionário com resultados formatados
    """
    # Carregar índice vetorial
    print(f"Carregando índice de: {embeddings_path}")
    index = VectorIndex.from_jsonl(embeddings_path)
    
    # Carregar perguntas
    print(f"Carregando perguntas de: {questions_path}")
    questions = load_questions(questions_path)
    
    # Configurar filtro
    status_filter = "vigente" if use_version_filter else None
    filter_mode = "WITH_VERSION_FILTER" if use_version_filter else "WITHOUT_VERSION_FILTER"
    print(f"Modo: {filter_mode}")
    
    # Avaliar cada pergunta
    query_evaluations = []
    
    for i, question in enumerate(questions, start=1):
        print(f"\n[{i}/{len(questions)}] Processando: {question['query'][:50]}...")
        
        # Executar retrieval
        retrieved_results = run_retrieval(
            index=index,
            query_text=question["query"],
            top_k=max(TOP_K_VALUES),
            status_filter=status_filter,
            aws_region=aws_region
        )
        
        # Avaliar
        evaluation = evaluate_query(
            query_spec=question,
            retrieved_results=retrieved_results,
            top_k_values=TOP_K_VALUES
        )
        
        query_evaluations.append(evaluation)
        
        print(f"  Version found: {evaluation.version_found_by_k}")
        print(f"  Revoked found: {evaluation.revoked_found_by_k}")
        if evaluation.version_rank:
            print(f"  Version rank: {evaluation.version_rank}")
    
    # Calcular resumo
    summary = calculate_summary(query_evaluations, TOP_K_VALUES)
    
    # Formatar resultados
    results = format_results(summary)
    results["filter_mode"] = filter_mode
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark de recuperação de versão vigente no RAG"
    )
    parser.add_argument(
        "--embeddings-path",
        type=str,
        default=str(DEFAULT_EMBEDDINGS_PATH),
        help="Caminho para o arquivo de embeddings JSONL"
    )
    parser.add_argument(
        "--questions-path",
        type=str,
        default=str(DEFAULT_QUESTIONS_PATH),
        help="Caminho para o arquivo de perguntas JSON"
    )
    parser.add_argument(
        "--with-filter",
        action="store_true",
        help="Usar filtro de status='vigente' (WITH_VERSION_FILTER)"
    )
    parser.add_argument(
        "--without-filter",
        action="store_true",
        help="Não usar filtro de status (WITHOUT_VERSION_FILTER)"
    )
    parser.add_argument(
        "--aws-region",
        type=str,
        default="us-east-1",
        help="Região AWS para Bedrock"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Caminho para salvar resultados JSON"
    )
    
    args = parser.parse_args()
    
    # Validar argumentos
    if not args.with_filter and not args.without_filter:
        print("Erro: especifique --with-filter ou --without-filter")
        print("Execute os dois separadamente para comparação.")
        return 1
    
    use_version_filter = args.with_filter
    
    # Executar benchmark
    try:
        results = run_benchmark(
            embeddings_path=Path(args.embeddings_path),
            questions_path=Path(args.questions_path),
            use_version_filter=use_version_filter,
            aws_region=args.aws_region
        )
        
        # Exibir resumo
        print("\n" + "="*60)
        print("RESUMO DO BENCHMARK")
        print("="*60)
        print(f"Modo: {results['filter_mode']}")
        print(f"Total de consultas: {results['total_queries']}")
        print("\nVersion Accuracy:")
        for k, acc in results['version_accuracy_by_k'].items():
            print(f"  {k}: {acc}")
        print("\nRevoked Contamination:")
        for k, cont in results['revoked_contamination_by_k'].items():
            print(f"  {k}: {cont}")
        
        # Salvar resultados se solicitado
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"\nResultados salvos em: {args.output}")
        
        return 0
        
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())