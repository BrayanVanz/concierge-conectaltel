"""
Testes unitários para o módulo evaluator.

Cenários de teste:
- vigente no Top-1
- vigente no Top-3
- vigente ausente
- apenas revogada
- vigente e revogada juntas
"""
import unittest
from eval.golden_sets.benchmark_version_retrieval.evaluator import (
    evaluate_query,
    calculate_summary,
    format_results
)


class EvaluatorTest(unittest.TestCase):
    """Testes das métricas de avaliação."""
    
    def setUp(self):
        """Configuração comum para os testes."""
        self.top_k_values = [1, 3, 5]
        self.query_spec = {
            "query": "Pergunta de teste",
            "expected_doc_family_id": "pol-reembolso",
            "expected_version": 2,
            "category": "teste"
        }
    
    def test_vigente_no_top_1(self):
        """Teste: versão vigente aparece no Top-1."""
        retrieved_results = [
            {
                "chunk_id": "pol-reembolso_v2_chunk1",
                "score": 0.95,
                "rank": 1,
                "metadata": {
                    "doc_family_id": "pol-reembolso",
                    "version_ordinal": 2,
                    "status": "vigente"
                }
            },
            {
                "chunk_id": "other_doc_chunk1",
                "score": 0.80,
                "rank": 2,
                "metadata": {
                    "doc_family_id": "other-doc",
                    "version_ordinal": 1,
                    "status": "vigente"
                }
            }
        ]
        
        evaluation = evaluate_query(
            self.query_spec,
            retrieved_results,
            self.top_k_values
        )
        
        # Version Accuracy @1 deve ser True
        self.assertTrue(evaluation.version_found_by_k[1])
        # Version Accuracy @3 deve ser True
        self.assertTrue(evaluation.version_found_by_k[3])
        # Version rank deve ser 1
        self.assertEqual(evaluation.version_rank, 1)
        # Não deve ter contaminação
        self.assertFalse(evaluation.revoked_found_by_k[1])
    
    def test_vigente_no_top_3(self):
        """Teste: versão vigente aparece no Top-3 (não no Top-1)."""
        retrieved_results = [
            {
                "chunk_id": "other_doc_chunk1",
                "score": 0.90,
                "rank": 1,
                "metadata": {
                    "doc_family_id": "other-doc",
                    "version_ordinal": 1,
                    "status": "vigente"
                }
            },
            {
                "chunk_id": "other_doc_chunk2",
                "score": 0.85,
                "rank": 2,
                "metadata": {
                    "doc_family_id": "other-doc",
                    "version_ordinal": 1,
                    "status": "vigente"
                }
            },
            {
                "chunk_id": "pol-reembolso_v2_chunk1",
                "score": 0.80,
                "rank": 3,
                "metadata": {
                    "doc_family_id": "pol-reembolso",
                    "version_ordinal": 2,
                    "status": "vigente"
                }
            }
        ]
        
        evaluation = evaluate_query(
            self.query_spec,
            retrieved_results,
            self.top_k_values
        )
        
        # Version Accuracy @1 deve ser False
        self.assertFalse(evaluation.version_found_by_k[1])
        # Version Accuracy @3 deve ser True
        self.assertTrue(evaluation.version_found_by_k[3])
        # Version rank deve ser 3
        self.assertEqual(evaluation.version_rank, 3)
    
    def test_vigente_ausente(self):
        """Teste: versão vigente não aparece nos resultados."""
        retrieved_results = [
            {
                "chunk_id": "other_doc_chunk1",
                "score": 0.90,
                "rank": 1,
                "metadata": {
                    "doc_family_id": "other-doc",
                    "version_ordinal": 1,
                    "status": "vigente"
                }
            },
            {
                "chunk_id": "other_doc_chunk2",
                "score": 0.85,
                "rank": 2,
                "metadata": {
                    "doc_family_id": "other-doc",
                    "version_ordinal": 1,
                    "status": "vigente"
                }
            }
        ]
        
        evaluation = evaluate_query(
            self.query_spec,
            retrieved_results,
            self.top_k_values
        )
        
        # Version Accuracy deve ser False para todos os K
        self.assertFalse(evaluation.version_found_by_k[1])
        self.assertFalse(evaluation.version_found_by_k[3])
        self.assertFalse(evaluation.version_found_by_k[5])
        # Version rank deve ser None
        self.assertIsNone(evaluation.version_rank)
    
    def test_apenas_revogada(self):
        """Teste: apenas versão revogada aparece nos resultados."""
        retrieved_results = [
            {
                "chunk_id": "pol-reembolso_v1_chunk1",
                "score": 0.90,
                "rank": 1,
                "metadata": {
                    "doc_family_id": "pol-reembolso",
                    "version_ordinal": 1,
                    "status": "revogado"
                }
            }
        ]
        
        evaluation = evaluate_query(
            self.query_spec,
            retrieved_results,
            self.top_k_values
        )
        
        # Version Accuracy deve ser False
        self.assertFalse(evaluation.version_found_by_k[1])
        # Deve ter contaminação por revogada
        self.assertTrue(evaluation.revoked_found_by_k[1])
        # Version rank deve ser None (versão vigente não encontrada)
        self.assertIsNone(evaluation.version_rank)
        # Versões recuperadas deve conter apenas 1
        self.assertEqual(evaluation.retrieved_versions, [1])
    
    def test_vigente_e_revogada_juntas(self):
        """Teste: versão vigente e revogada aparecem juntas."""
        retrieved_results = [
            {
                "chunk_id": "pol-reembolso_v1_chunk1",
                "score": 0.95,
                "rank": 1,
                "metadata": {
                    "doc_family_id": "pol-reembolso",
                    "version_ordinal": 1,
                    "status": "revogado"
                }
            },
            {
                "chunk_id": "pol-reembolso_v2_chunk1",
                "score": 0.90,
                "rank": 2,
                "metadata": {
                    "doc_family_id": "pol-reembolso",
                    "version_ordinal": 2,
                    "status": "vigente"
                }
            }
        ]
        
        evaluation = evaluate_query(
            self.query_spec,
            retrieved_results,
            self.top_k_values
        )
        
        # Version Accuracy @1 deve ser False (revogada em 1º)
        self.assertFalse(evaluation.version_found_by_k[1])
        # Version Accuracy @3 deve ser True (vigente em 2º)
        self.assertTrue(evaluation.version_found_by_k[3])
        # Version rank deve ser 2
        self.assertEqual(evaluation.version_rank, 2)
        # Deve ter contaminação por revogada
        self.assertTrue(evaluation.revoked_found_by_k[1])
        self.assertTrue(evaluation.revoked_found_by_k[3])
        # Versões recuperadas deve conter ambas
        self.assertEqual(sorted(evaluation.retrieved_versions), [1, 2])
    
    def test_calculate_summary(self):
        """Teste: cálculo do resumo do benchmark."""
        # Criar avaliações simuladas
        from eval.golden_sets.benchmark_version_retrieval.evaluator import QueryEvaluation
        
        eval1 = QueryEvaluation(
            query="Query 1",
            expected_doc_family_id="pol-reembolso",
            expected_version=2,
            category="teste",
            version_found_by_k={1: True, 3: True, 5: True},
            revoked_found_by_k={1: False, 3: False, 5: False},
            version_rank=1
        )
        
        eval2 = QueryEvaluation(
            query="Query 2",
            expected_doc_family_id="pol-reembolso",
            expected_version=2,
            category="teste",
            version_found_by_k={1: False, 3: True, 5: True},
            revoked_found_by_k={1: True, 3: True, 5: True},
            version_rank=2
        )
        
        summary = calculate_summary([eval1, eval2], self.top_k_values)
        
        # Total de consultas
        self.assertEqual(summary.total_queries, 2)
        
        # Version Accuracy @1: 1/2 = 0.5
        self.assertEqual(summary.version_accuracy_by_k[1], 0.5)
        # Version Accuracy @3: 2/2 = 1.0
        self.assertEqual(summary.version_accuracy_by_k[3], 1.0)
        
        # Revoked Contamination @1: 1/2 = 0.5
        self.assertEqual(summary.revoked_contamination_by_k[1], 0.5)
        # Revoked Contamination @3: 1/2 = 0.5
        self.assertEqual(summary.revoked_contamination_by_k[3], 0.5)
        
        # Version ranks
        self.assertEqual(summary.version_ranks, [1, 2])
    
    def test_format_results(self):
        """Teste: formatação dos resultados para exportação."""
        from eval.golden_sets.benchmark_version_retrieval.evaluator import QueryEvaluation, BenchmarkSummary
        
        eval1 = QueryEvaluation(
            query="Query 1",
            expected_doc_family_id="pol-reembolso",
            expected_version=2,
            category="teste",
            version_found_by_k={1: True},
            revoked_found_by_k={1: False},
            version_rank=1,
            retrieved_versions=[2]
        )
        
        summary = BenchmarkSummary(
            total_queries=1,
            version_accuracy_by_k={1: 1.0},
            revoked_contamination_by_k={1: 0.0},
            version_ranks=[1],
            query_evaluations=[eval1]
        )
        
        results = format_results(summary)
        
        # Verificar estrutura
        self.assertIn("total_queries", results)
        self.assertIn("version_accuracy_by_k", results)
        self.assertIn("revoked_contamination_by_k", results)
        self.assertIn("individual_results", results)
        
        # Verificar valores
        self.assertEqual(results["total_queries"], 1)
        self.assertEqual(results["version_accuracy_by_k"]["@1"], "1.0000")
        self.assertEqual(results["revoked_contamination_by_k"]["@1"], "0.0000")
        
        # Verificar resultado individual
        self.assertEqual(len(results["individual_results"]), 1)
        individual = results["individual_results"][0]
        self.assertEqual(individual["query"], "Query 1")
        self.assertEqual(individual["expected_version"], 2)


if __name__ == "__main__":
    unittest.main()