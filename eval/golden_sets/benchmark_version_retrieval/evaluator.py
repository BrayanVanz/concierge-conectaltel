"""
Módulo de avaliação para benchmark de recuperação de versão vigente.

Implementa as métricas:
- Version Accuracy @K
- Revoked Contamination @K  
- Version Rank
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class QueryEvaluation:
    """Resultado da avaliação de uma única consulta."""
    query: str
    expected_doc_family_id: str
    expected_version: int
    category: str
    
    # Resultados do retrieval
    retrieved_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Métricas por top_k
    version_found_by_k: Dict[int, bool] = field(default_factory=dict)
    revoked_found_by_k: Dict[int, bool] = field(default_factory=dict)
    
    # Version rank (posição onde a versão vigente aparece, se encontrada)
    version_rank: Optional[int] = None
    
    # Versões recuperadas (para análise)
    retrieved_versions: List[int] = field(default_factory=list)


@dataclass
class BenchmarkSummary:
    """Resumo consolidado do benchmark."""
    total_queries: int
    
    # Version Accuracy @K
    version_accuracy_by_k: Dict[int, float]
    
    # Revoked Contamination @K
    revoked_contamination_by_k: Dict[int, float]
    
    # Version ranks (para análise)
    version_ranks: List[int]
    
    # Resultados individuais
    query_evaluations: List[QueryEvaluation]


def evaluate_query(
    query_spec: Dict[str, Any],
    retrieved_results: List[Dict[str, Any]],
    top_k_values: List[int]
) -> QueryEvaluation:
    """
    Avalia uma consulta individual.
    
    Args:
        query_spec: Dicionário com query, expected_doc_family_id, expected_version, category
        retrieved_results: Lista de resultados do retrieval (com rank, score, metadata)
        top_k_values: Lista de valores de K para calcular métricas
    
    Returns:
        QueryEvaluation com métricas calculadas
    """
    expected_family = query_spec["expected_doc_family_id"]
    expected_version = query_spec["expected_version"]
    
    evaluation = QueryEvaluation(
        query=query_spec["query"],
        expected_doc_family_id=expected_family,
        expected_version=expected_version,
        category=query_spec["category"],
        retrieved_results=retrieved_results
    )
    
    # Extrair versões recuperadas da família esperada
    retrieved_versions = []
    for result in retrieved_results:
        metadata = result.get("metadata", {})
        if metadata.get("doc_family_id") == expected_family:
            version = metadata.get("version_ordinal")
            if version is not None:
                retrieved_versions.append(version)
    
    evaluation.retrieved_versions = retrieved_versions
    
    # Encontrar posição da versão vigente (version rank)
    for i, result in enumerate(retrieved_results, start=1):
        metadata = result.get("metadata", {})
        if (metadata.get("doc_family_id") == expected_family and 
            metadata.get("version_ordinal") == expected_version):
            evaluation.version_rank = i
            break
    
    # Calcular métricas por top_k
    for k in top_k_values:
        top_k_results = retrieved_results[:k]
        
        # Version Accuracy @K: versão vigente está no top_k?
        version_found = any(
            result.get("metadata", {}).get("doc_family_id") == expected_family and
            result.get("metadata", {}).get("version_ordinal") == expected_version
            for result in top_k_results
        )
        evaluation.version_found_by_k[k] = version_found
        
        # Revoked Contamination @K: há versão revogada da mesma família no top_k?
        revoked_found = any(
            result.get("metadata", {}).get("doc_family_id") == expected_family and
            result.get("metadata", {}).get("status") == "revogado"
            for result in top_k_results
        )
        evaluation.revoked_found_by_k[k] = revoked_found
    
    return evaluation


def calculate_summary(
    query_evaluations: List[QueryEvaluation],
    top_k_values: List[int]
) -> BenchmarkSummary:
    """
    Calcula o resumo do benchmark a partir das avaliações individuais.
    
    Args:
        query_evaluations: Lista de QueryEvaluation
        top_k_values: Lista de valores de K usados
    
    Returns:
        BenchmarkSummary com métricas consolidadas
    """
    total = len(query_evaluations)
    
    # Version Accuracy @K
    version_accuracy_by_k = {}
    for k in top_k_values:
        hits = sum(1 for eval in query_evaluations if eval.version_found_by_k.get(k, False))
        version_accuracy_by_k[k] = hits / total if total > 0 else 0.0
    
    # Revoked Contamination @K
    revoked_contamination_by_k = {}
    for k in top_k_values:
        contaminated = sum(1 for eval in query_evaluations if eval.revoked_found_by_k.get(k, False))
        revoked_contamination_by_k[k] = contaminated / total if total > 0 else 0.0
    
    # Version ranks (apenas onde a versão foi encontrada)
    version_ranks = [
        eval.version_rank 
        for eval in query_evaluations 
        if eval.version_rank is not None
    ]
    
    return BenchmarkSummary(
        total_queries=total,
        version_accuracy_by_k=version_accuracy_by_k,
        revoked_contamination_by_k=revoked_contamination_by_k,
        version_ranks=version_ranks,
        query_evaluations=query_evaluations
    )


def format_results(summary: BenchmarkSummary) -> Dict[str, Any]:
    """
    Formata os resultados para exportação (JSON/CSV).
    
    Args:
        summary: BenchmarkSummary com resultados
    
    Returns:
        Dicionário com resultados formatados
    """
    # Resultados individuais por consulta
    individual_results = []
    for eval in summary.query_evaluations:
        individual_results.append({
            "query": eval.query,
            "expected_version": eval.expected_version,
            "retrieved_versions": eval.retrieved_versions,
            "version_found": eval.version_found_by_k,
            "version_rank": eval.version_rank,
            "revoked_found": eval.revoked_found_by_k,
            "category": eval.category
        })
    
    # Resumo
    summary_dict = {
        "total_queries": summary.total_queries,
        "version_accuracy_by_k": {
            f"@{k}": f"{rate:.4f}" 
            for k, rate in summary.version_accuracy_by_k.items()
        },
        "revoked_contamination_by_k": {
            f"@{k}": f"{rate:.4f}" 
            for k, rate in summary.revoked_contamination_by_k.items()
        },
        "version_ranks": summary.version_ranks,
        "individual_results": individual_results
    }
    
    return summary_dict