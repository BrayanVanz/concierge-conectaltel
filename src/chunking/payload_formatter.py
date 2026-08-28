from typing import Any, Dict, List

def format_chunk_payload(
    chunk: Dict[str, Any],
    strategy: str,
    chunk_index: int
) -> Dict[str, Any]:
    """
    Padroniza o payload de saída para cada chunk gerado pelas estratégias.
    
    Garante que todo chunk contenha a estrutura de dicionário exigida com
    chunk_id formatado e metadados completos incluindo strategy e chunk_index.
    
    Args:
        chunk: Chunk gerado por uma das estratégias de chunking
        strategy: Nome da estratégia usada ("fixed_window", "full_document", "hierarchical_semantic")
        chunk_index: Índice do chunk dentro do documento
        
    Returns:
        Chunk com payload padronizado
    """
    doc_family_id = chunk["metadata"]["doc_family_id"]
    version_ordinal = chunk["metadata"]["version_ordinal"]
    
    # Formata chunk_id no padrão exigido
    chunk_id = f"{doc_family_id}_v{version_ordinal}_{strategy}_{chunk_index:03d}"
    
    # Adiciona strategy e chunk_index aos metadados
    formatted_metadata = chunk["metadata"].copy()
    formatted_metadata["strategy"] = strategy
    formatted_metadata["chunk_index"] = chunk_index
    
    return {
        "chunk_id": chunk_id,
        "text_content": chunk["text_content"],
        "metadata": formatted_metadata
    }

def format_strategy_payloads(
    chunks: List[Dict[str, Any]],
    strategy: str
) -> List[Dict[str, Any]]:
    """
    Padroniza todos os chunks gerados por uma estratégia específica.
    
    Args:
        chunks: Lista de chunks gerados por uma estratégia
        strategy: Nome da estratégia usada
        
    Returns:
        Lista de chunks com payloads padronizados
    """
    formatted_chunks = []
    
    for chunk_index, chunk in enumerate(chunks):
        formatted_chunk = format_chunk_payload(chunk, strategy, chunk_index)
        formatted_chunks.append(formatted_chunk)
    
    return formatted_chunks