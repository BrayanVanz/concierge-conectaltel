"""
Script de avaliação da Estratégias de Chunking
"""
import os
import sys
import json
import pandas as pd
from typing import List, Dict, Any
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import ContextPrecision, Faithfulness
from ragas.metrics._context_recall import context_recall
from ragas.run_config import RunConfig
from langchain_aws import ChatBedrock
from ragas.llms import LangchainLLMWrapper

# Adiciona a raiz do projeto ao PATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.agent import ConciergeAgent


def load_golden_dataset(dataset_path: str) -> List[Dict[str, Any]]:
    """Carrega o dataset de testes do arquivo JSON."""
    with open(dataset_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def calculate_version_accuracy(chunks_list: List[List[Dict]]) -> float:
    """
    Calcula a porcentagem de chunks recuperados com status 'vigente'.
    
    Args:
        chunks_list: Lista de listas de chunks (para cada pergunta)
    
    Returns:
        Porcentagem de chunks com status 'vigente' (0.0 a 1.0)
    """
    total_chunks = 0
    vigente_chunks = 0
    
    for chunks in chunks_list:
        for chunk in chunks:
            total_chunks += 1
            # Verifica se o chunk tem status 'vigente' ou se é omitido (assume vigente)
            status = chunk.get('status', 'vigente')
            if status == 'vigente':
                vigente_chunks += 1
    
    if total_chunks == 0:
        return 0.0
    
    return vigente_chunks / total_chunks


def evaluate_strategy(
    agent: ConciergeAgent,
    golden_dataset: List[Dict[str, Any]],
    strategy_name: str,
    top_k: int = 3
) -> Dict[str, Any]:
    """
    Avalia uma estratégia de chunking específica.
    
    Args:
        agent: Instância do ConciergeAgent configurada com a estratégia
        golden_dataset: Lista de perguntas do golden dataset
        strategy_name: Nome da estratégia para identificação
        top_k: Número de chunks a recuperar
    
    Returns:
        Dicionário com resultados da avaliação
    """
    print(f"\n{'='*60}")
    print(f"Avaliando estratégia: {strategy_name}")
    print(f"{'='*60}")
    
    questions = []
    contexts = []
    answers = []
    ground_truths = []
    all_chunks = [] 
    
    for item in golden_dataset:
        question = item['pergunta']
        expected_answer = item['resposta_esperada']
        
        print(f"\nProcessando pergunta: {question[:50]}...")
        
        # Recupera chunks diretamente para obter metadados
        chunks = agent.retrieve_vigente_chunks(question, top_k=top_k)
        all_chunks.append(chunks)
        
        chunk_texts = []
        for chunk in chunks:
            content = chunk.get('content', '')
            if isinstance(content, str):
                chunk_texts.append(content.strip())
            else:
                # Converte para string se não for
                chunk_texts.append(str(content).strip())
        
        # Garante que temos pelo menos um contexto vazio se nenhum chunk foi recuperado
        if not chunk_texts:
            chunk_texts = ["Nenhum contexto disponível"]
        
        contexts.append(chunk_texts)
        
        # Gera a resposta usando o agente
        result = agent.process_message(
            user_id="eval_user",
            query=question,
            conversation_history=[]
        )
        
        generated_answer = result.get('response', '')
        
        # Armazena dados para Ragas
        questions.append(question)
        answers.append(generated_answer)
        ground_truths.append(expected_answer)
        
        print(f"  ✓ Chunks recuperados: {len(chunks)}")
        print(f"  ✓ Resposta gerada: {generated_answer[:50]}...")
    
    # Calcula Version Accuracy (métrica customizada)
    version_accuracy = calculate_version_accuracy(all_chunks)
    
    # Prepara dataset para Ragas com formatação correta
    ragas_dataset = Dataset.from_dict({
        "question": questions,
        "contexts": contexts,  # Deve ser List[List[str]]
        "answer": answers,
        "ground_truth": ground_truths  # Deve ser List[str], não List[List[str]]
    })
    
    # Configura LLM do Ragas para usar Bedrock diretamente
    bedrock_llm = ChatBedrock(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        region_name="us-east-1"
    )
    
    # Configuração de execução opcional
    run_config = RunConfig(timeout=60, max_retries=3)
    
    # Executa avaliação Ragas com todas as métricas
    print(f"\nCalculando métricas Ragas (Context Precision, Faithfulness, Context Recall)...")
    try:
        ragas_result = evaluate(
            ragas_dataset,
            metrics=[
                ContextPrecision(), 
                Faithfulness(),
                context_recall,
            ],
            llm=bedrock_llm,
            run_config=run_config
        )
        
        if ragas_result is None:
            raise ValueError("A avaliação do Ragas retornou None.")
            
        ragas_df = ragas_result.to_pandas()
        if ragas_df is None or ragas_df.empty:
            raise ValueError("O DataFrame do Ragas está vazio ou é None.")
            
        # Extrai scores das métricas
        context_precision_score = ragas_df['context_precision'].mean() if 'context_precision' in ragas_df else 0.0
        faithfulness_score = ragas_df['faithfulness'].mean() if 'faithfulness' in ragas_df else 0.0
        context_recall_score = ragas_df['context_recall'].mean() if 'context_recall' in ragas_df else 0.0
        
    except Exception as e:
        print(f"Erro ao calcular métricas Ragas: {e}")
        context_precision_score = 0.0
        faithfulness_score = 0.0
        context_recall_score = 0.0
        ragas_df = None
    
    print(f"\n{'='*60}")
    print(f"Resultados para {strategy_name}:")
    print(f"  Context Precision: {context_precision_score:.4f}")
    print(f"  Faithfulness: {faithfulness_score:.4f}")
    print(f"  Context Recall: {context_recall_score:.4f}")
    print(f"  Version Accuracy: {version_accuracy:.4f}")
    print(f"{'='*60}")
    
    return {
        "strategy": strategy_name,
        "context_precision": context_precision_score,
        "faithfulness": faithfulness_score,
        "context_recall": context_recall_score,
        "version_accuracy": version_accuracy,
        "ragas_details": ragas_df
    }


def main():
    """Função principal de orquestração da avaliação."""
    # Configurações
    golden_dataset_path = os.path.join(
        os.path.dirname(__file__), 
        "golden_sets", 
        "golden_set_chunking.json"
    )
    
    strategies = ["fixed_windows", "full_document", "hierarchical_semantic"]
    score_threshold = 0.68 # Limiar padrão do CLI
    top_k = 3  # Número de chunks a recuperar
    
    # Carrega o golden dataset
    print(f"Carregando golden dataset de: {golden_dataset_path}")
    golden_dataset = load_golden_dataset(golden_dataset_path)
    print(f"✓ {len(golden_dataset)} perguntas carregadas")
    
    # Avalia cada estratégia
    results = []
    
    for strategy in strategies:
        try:
            # Inicializa agente com a estratégia atual
            agent = ConciergeAgent(
                chunk_strategy=strategy,
                score_threshold=score_threshold
            )
            
            # Executa avaliação
            strategy_result = evaluate_strategy(
                agent=agent,
                golden_dataset=golden_dataset,
                strategy_name=strategy,
                top_k=top_k
            )
            
            results.append(strategy_result)
            
        except Exception as e:
            print(f"\n❌ Erro ao avaliar estratégia {strategy}: {e}")
            results.append({
                "strategy": strategy,
                "context_precision": 0.0,
                "faithfulness": 0.0,
                "context_recall": 0.0,
                "version_accuracy": 0.0,
                "error": str(e)
            })
    
    # Cria tabela comparativa final
    print(f"\n{'='*80}")
    print("TABELA COMPARATIVA FINAL - MÉTRICAS POR ESTRATÉGIA")
    print(f"{'='*80}")
    
    valid_results = [r for r in results if r is not None and isinstance(r, dict)]
    
    if not valid_results:
        print("❌ Nenhum resultado válido foi gerado para exibir na tabela.")
        return

    comparison_df = pd.DataFrame([
        {
            "Estratégia": r.get("strategy", "desconhecida"),
            "Context Precision": f"{r.get('context_precision', 0.0):.4f}",
            "Faithfulness": f"{r.get('faithfulness', 0.0):.4f}",
            "Context Recall": f"{r.get('context_recall', 0.0):.4f}",
            "Version Accuracy": f"{r.get('version_accuracy', 0.0):.4f}"
        }
        for r in valid_results
    ])
    
    print(comparison_df.to_string(index=False))


if __name__ == "__main__":
    main()