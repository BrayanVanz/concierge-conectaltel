import re
from typing import Any, Dict, List


def _generate_chunk_id(doc_family_id: str, version_ordinal: int, chunk_index: int) -> str:
    """
    Gera um ID único para o chunk baseado nos metadados do documento.
    """
    return f"{doc_family_id}_v{version_ordinal}_chunk{chunk_index}"


def _inject_header(title: str, doc_family_id: str, version_ordinal: int, status: str) -> str:
    """
    Gera o cabeçalho padrão a ser injetado no topo de cada chunk.
    """
    return f"[DOCUMENTO: {title} | FAMÍLIA: {doc_family_id} | VERSÃO: {version_ordinal} | STATUS: {status}]\n\n"

def chunk_fixed_window(documents: List[Dict[str, Any]], chunk_size: int = 500, overlap: int = 100) -> List[Dict[str, Any]]:
    """
    Divide o conteúdo de cada documento em janelas fixas com sobreposição.
    
    Args:
        documents: Lista de documentos com metadados e conteúdo
        chunk_size: Tamanho de cada chunk em caracteres
        overlap: Sobreposição entre chunks consecutivos
        
    Returns:
        Lista de chunks gerados
    """
    chunks = []
    
    for doc in documents:
        content = doc.get("content", "")
        chunk_index = 0
        
        if len(content) <= chunk_size:
            # Conteúdo menor que o chunk size - chunk único
            chunk_id = _generate_chunk_id(
                doc["doc_family_id"],
                doc["version_ordinal"],
                chunk_index
            )
            
            chunks.append({
                "chunk_id": chunk_id,
                "text_content": content,
                "metadata": {
                    "doc_family_id": doc["doc_family_id"],
                    "version_ordinal": doc["version_ordinal"],
                    "effective_from": doc["effective_from"],
                    "effective_to": doc["effective_to"],
                    "status": doc["status"],
                    "title": doc["title"]
                }
            })
            chunk_index += 1
        else:
            # Divide em múltiplos chunks com sobreposição
            start = 0
            while start < len(content):
                end = min(start + chunk_size, len(content))
                
                chunk_id = _generate_chunk_id(
                    doc["doc_family_id"],
                    doc["version_ordinal"],
                    chunk_index
                )
                
                chunks.append({
                    "chunk_id": chunk_id,
                    "text_content": content[start:end],
                    "metadata": {
                        "doc_family_id": doc["doc_family_id"],
                        "version_ordinal": doc["version_ordinal"],
                        "effective_from": doc["effective_from"],
                        "effective_to": doc["effective_to"],
                        "status": doc["status"],
                        "title": doc["title"]
                    }
                })
                
                chunk_index += 1
                start = end - overlap  # Aplica sobreposição
    
    return chunks


def chunk_full_document(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Retorna cada documento inteiro como um único chunk.
    
    Args:
        documents: Lista de documentos com metadados e conteúdo
        
    Returns:
        Lista de chunks (um por documento)
    """
    chunks = []
    
    for doc in documents:
        chunk_id = _generate_chunk_id(
            doc["doc_family_id"],
            doc["version_ordinal"],
            0
        )
        
        chunks.append({
            "chunk_id": chunk_id,
            "text_content": doc.get("content", ""),
            "metadata": {
                "doc_family_id": doc["doc_family_id"],
                "version_ordinal": doc["version_ordinal"],
                "effective_from": doc["effective_from"],
                "effective_to": doc["effective_to"],
                "status": doc["status"],
                "title": doc["title"]
            }
        })
    
    return chunks


def chunk_hierarchical_semantic(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Divide documentos de forma hierárquica baseada em tipo e estrutura semântica.
    
    - FAQ: Divide por pares Pergunta/Resposta
    - Planos/Políticas/Procedimentos: Divide por seções H2
    
    Mantém integridade de tabelas e listas numeradas.
    Injeta cabeçalho padrão em cada chunk.
    
    Args:
        documents: Lista de documentos com metadados e conteúdo
        
    Returns:
        Lista de chunks gerados
    """
    chunks = []
    
    for doc in documents:
        doc_family_id = doc.get("doc_family_id", "")
        content = doc.get("content", "")
        chunk_index = 0
        
        # Determina tipo de documento
        is_faq = doc_family_id.lower().startswith("faq")
        
        # Gera cabeçalho padrão
        header = _inject_header(
            doc["title"],
            doc["doc_family_id"],
            doc["version_ordinal"],
            doc["status"]
        )
        
        if is_faq:
            # Processa FAQ: divide por pares Pergunta/Resposta
            faq_chunks = _chunk_faq_content(content, header, doc, chunk_index)
            chunks.extend(faq_chunks)
        else:
            # Processa Planos/Políticas/Procedimentos: divide por seções H2
            section_chunks = _chunk_by_h2_sections(content, header, doc, chunk_index)
            chunks.extend(section_chunks)
    
    return chunks


def _chunk_faq_content(content: str, header: str, doc: Dict[str, Any], start_chunk_index: int) -> List[Dict[str, Any]]:
    """
    Divide conteúdo de FAQ em chunks por pares Pergunta/Resposta.
    """
    chunks = []
    chunk_index = start_chunk_index
    
    # Padrão para identificar perguntas em negrito
    question_pattern = r'\*\*[^*]+\*\*'
    
    # Encontra todas as posições das perguntas
    question_positions = []
    for match in re.finditer(question_pattern, content):
        question_positions.append(match.start())
    
    if not question_positions:
        # Se não encontrar perguntas, trata como documento único
        chunk_id = _generate_chunk_id(doc["doc_family_id"], doc["version_ordinal"], chunk_index)
        chunks.append({
            "chunk_id": chunk_id,
            "text_content": header + content,
            "metadata": {
                "doc_family_id": doc["doc_family_id"],
                "version_ordinal": doc["version_ordinal"],
                "effective_from": doc["effective_from"],
                "effective_to": doc["effective_to"],
                "status": doc["status"],
                "title": doc["title"]
            }
        })
        return chunks
    
    # Divide conteúdo entre perguntas
    for i, pos in enumerate(question_positions):
        start = pos
        # O próximo chunk começa na próxima pergunta ou no fim do conteúdo
        if i + 1 < len(question_positions):
            end = question_positions[i + 1]
        else:
            end = len(content)
        
        chunk_content = content[start:end].strip()
        
        if chunk_content:
            chunk_id = _generate_chunk_id(doc["doc_family_id"], doc["version_ordinal"], chunk_index)
            chunks.append({
                "chunk_id": chunk_id,
                "text_content": header + chunk_content,
                "metadata": {
                    "doc_family_id": doc["doc_family_id"],
                    "version_ordinal": doc["version_ordinal"],
                    "effective_from": doc["effective_from"],
                    "effective_to": doc["effective_to"],
                    "status": doc["status"],
                    "title": doc["title"]
                }
            })
            chunk_index += 1
    
    return chunks


def _chunk_by_h2_sections(content: str, header: str, doc: Dict[str, Any], start_chunk_index: int) -> List[Dict[str, Any]]:
    """
    Divide conteúdo por seções H2, respeitando integridade de tabelas e listas.
    """
    chunks = []
    chunk_index = start_chunk_index
    
    # Encontra todas as seções H2
    h2_pattern = r'^##\s+.+$'
    h2_positions = []
    
    for match in re.finditer(h2_pattern, content, re.MULTILINE):
        h2_positions.append(match.start())
    
    if not h2_positions:
        # Se não encontrar H2, trata como documento único
        chunk_id = _generate_chunk_id(doc["doc_family_id"], doc["version_ordinal"], chunk_index)
        chunks.append({
            "chunk_id": chunk_id,
            "text_content": header + content,
            "metadata": {
                "doc_family_id": doc["doc_family_id"],
                "version_ordinal": doc["version_ordinal"],
                "effective_from": doc["effective_from"],
                "effective_to": doc["effective_to"],
                "status": doc["status"],
                "title": doc["title"]
            }
        })
        return chunks
    
    # Divide conteúdo entre seções H2
    for i, pos in enumerate(h2_positions):
        start = pos
        # O próximo chunk começa na próxima seção H2 ou no fim do conteúdo
        if i + 1 < len(h2_positions):
            end = h2_positions[i + 1]
        else:
            end = len(content)
        
        chunk_content = content[start:end].strip()
        
        # Verifica integridade - se chunk corta tabela ou lista, ajusta
        chunk_content = _ensure_integrity(chunk_content, content, start, end)
        
        if chunk_content:
            chunk_id = _generate_chunk_id(doc["doc_family_id"], doc["version_ordinal"], chunk_index)
            chunks.append({
                "chunk_id": chunk_id,
                "text_content": header + chunk_content,
                "metadata": {
                    "doc_family_id": doc["doc_family_id"],
                    "version_ordinal": doc["version_ordinal"],
                    "effective_from": doc["effective_from"],
                    "effective_to": doc["effective_to"],
                    "status": doc["status"],
                    "title": doc["title"]
                }
            })
            chunk_index += 1
    
    return chunks


def _ensure_integrity(chunk_content: str, full_content: str, chunk_start: int, chunk_end: int) -> str:
    """
    Ajusta o chunk para não cortar tabelas ou listas numeradas.
    Se o corte acontece no meio de uma tabela/lista, estende o chunk até o fim da estrutura.
    """
    lines = chunk_content.split('\n')
    
    # Verifica se a última linha é parte de uma tabela
    if lines and '|' in lines[-1]:
        # Encontra o fim da tabela no conteúdo completo
        remaining_content = full_content[chunk_end:]
        table_end = chunk_end
        
        for i, line in enumerate(remaining_content.split('\n')):
            if '|' in line:
                table_end = chunk_end + i * len(line) + len(line)
            else:
                break
        
        if table_end > chunk_end:
            return full_content[chunk_start:table_end].strip()
    
    # Verifica se a última linha é parte de uma lista numerada
    if lines and re.match(r'^\s*\d+\.', lines[-1]):
        # Encontra o fim da lista no conteúdo completo
        remaining_content = full_content[chunk_end:]
        list_end = chunk_end
        
        for i, line in enumerate(remaining_content.split('\n')):
            if re.match(r'^\s*\d+\.', line):
                list_end = chunk_end + i * len(line) + len(line)
            else:
                break
        
        if list_end > chunk_end:
            return full_content[chunk_start:list_end].strip()
    
    return chunk_content