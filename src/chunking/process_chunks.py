import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from chunk_strategies import chunk_fixed_window, chunk_full_document, chunk_hierarchical_semantic

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("process_chunks_local")


def transform_document_structure(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforma a estrutura do documento do formato de ingestão para o formato esperado pelas estratégias de chunking.
    
    Move os campos do metadata para o nível superior e extrai o título do conteúdo.
    
    Args:
        doc: Documento no formato de ingestão (com campos aninhados em metadata)
        
    Returns:
        Documento transformado com campos no nível superior
    """
    # Extrai campos do metadata
    metadata = doc.get("metadata", {})
    
    # Extrai título do conteúdo (primeira linha se começar com #)
    content = doc.get("content", "")
    if content.strip().startswith('#'):
        title = content.split('\n')[0].replace('#', '').strip()
    else:
        title = doc.get("source", "")
    
    # Cria estrutura transformada
    transformed = {
        "doc_family_id": metadata.get("doc_family_id", f"doc_{doc.get('id', 0)}"),
        "version_ordinal": metadata.get("version_ordinal", 1),
        "effective_from": metadata.get("effective_from", ""),
        "effective_to": metadata.get("effective_to", ""),
        "status": metadata.get("status", "vigente"),
        "title": title,
        "content": content
    }
    
    return transformed


def read_jsonl_local(file_path: str) -> List[Dict[str, Any]]:
    """
    Lê um arquivo .jsonl do disco local e carrega o conteúdo parseado em memória.
    
    Args:
        file_path: Caminho do arquivo .jsonl no disco local
        
    Returns:
        Lista de dicionários parseados do JSONL
    """
    logger.info(f"Lendo arquivo local: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
    
    logger.info(f"Arquivo lido: {len(file_content)} caracteres")
    
    # Parse cada linha do JSONL
    documents = []
    for line_num, line in enumerate(file_content.splitlines(), 1):
        if line.strip():  # Ignora linhas vazias
            try:
                doc = json.loads(line)
                # Transforma a estrutura do documento
                transformed_doc = transform_document_structure(doc)
                documents.append(transformed_doc)
            except json.JSONDecodeError as e:
                logger.error(f"Erro ao parsear linha {line_num}: {e}")
    
    logger.info(f"Total de registros carregados: {len(documents)}")
    
    return documents


def write_chunks_to_jsonl(chunks: List[Dict[str, Any]], output_path: str) -> None:
    """
    Escreve uma lista de chunks em formato JSONL no disco local.
    
    Args:
        chunks: Lista de chunks a serem escritos
        output_path: Caminho do arquivo de saída
    """
    logger.info(f"Escrevendo {len(chunks)} chunks em {output_path}")
    
    # Cria diretório se não existir
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Converte chunks para JSONL
    jsonl_lines = []
    for chunk in chunks:
        jsonl_lines.append(json.dumps(chunk, ensure_ascii=False))
    
    # Adiciona nova linha ao final se houver conteúdo
    if jsonl_lines:
        jsonl_content = "\n".join(jsonl_lines) + "\n"
    else:
        jsonl_content = ""
    
    # Escreve no arquivo
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(jsonl_content)
    
    logger.info(f"Arquivo escrito com sucesso: {output_path}")


def process_chunks_local(
    input_path: str = "data/processed/corpus.jsonl",
    output_dir: str = "data/chunks"
) -> Dict[str, int]:
    """
    Processa o corpus local aplicando as 3 estratégias de chunking.
    
    Args:
        input_path: Caminho do arquivo JSONL de entrada
        output_dir: Diretório de saída para os arquivos de chunks
        
    Returns:
        Dicionário com contagem de chunks por estratégia
    """
    # Lê documentos do arquivo local
    documents = read_jsonl_local(input_path)
    
    # Aplica estratégia 1: Janela Fixa
    logger.info("Aplicando estratégia: Janela Fixa")
    chunks_fixed_window = chunk_fixed_window(documents, chunk_size=500, overlap=100)
    output_path_fixed = f"{output_dir}/chunks_fixed_window.jsonl"
    write_chunks_to_jsonl(chunks_fixed_window, output_path_fixed)
    
    # Aplica estratégia 2: Documento Inteiro
    logger.info("Aplicando estratégia: Documento Inteiro")
    chunks_full_document = chunk_full_document(documents)
    output_path_full = f"{output_dir}/chunks_full_document.jsonl"
    write_chunks_to_jsonl(chunks_full_document, output_path_full)
    
    # Aplica estratégia 3: Hierárquico Semântico
    logger.info("Aplicando estratégia: Hierárquico Semântico")
    chunks_hierarchical = chunk_hierarchical_semantic(documents)
    output_path_hierarchical = f"{output_dir}/chunks_hierarchical_semantic.jsonl"
    write_chunks_to_jsonl(chunks_hierarchical, output_path_hierarchical)
    
    logger.info("Processamento concluído com sucesso")
    
    return {
        "fixed_window": len(chunks_fixed_window),
        "full_document": len(chunks_full_document),
        "hierarchical_semantic": len(chunks_hierarchical)
    }


if __name__ == "__main__":
    import sys
    
    # Permite caminho customizado via argumento de linha de comando
    input_path = sys.argv[1] if len(sys.argv) > 1 else "data/processed/corpus.jsonl"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "data/chunks"
    
    try:
        counts = process_chunks_local(input_path, output_dir)
        print(f"\nProcessamento concluído:")
        print(f"  - Janela Fixa: {counts['fixed_window']} chunks")
        print(f"  - Documento Inteiro: {counts['full_document']} chunks")
        print(f"  - Hierárquico Semântico: {counts['hierarchical_semantic']} chunks")
    except Exception as e:
        logger.exception("Erro ao processar chunks localmente")
        sys.exit(1)