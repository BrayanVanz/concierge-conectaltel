import json
import logging
from typing import Any, Dict, List

import boto3

logger = logging.getLogger("s3_writer")


def chunks_to_jsonl(chunks: List[Dict[str, Any]]) -> str:
    """
    Converte uma lista de chunks para formato JSONL (uma linha JSON por chunk).
    
    Args:
        chunks: Lista de chunks a serem convertidos
        
    Returns:
        String formatada em JSONL
    """
    jsonl_lines = []
    
    for chunk in chunks:
        jsonl_lines.append(json.dumps(chunk, ensure_ascii=False))
    
    # Adiciona nova linha ao final se houver conteúdo
    if jsonl_lines:
        return "\n".join(jsonl_lines) + "\n"
    
    return ""


def write_chunks_to_s3(
    chunks: List[Dict[str, Any]],
    bucket_name: str,
    file_key: str
) -> str:
    """
    Escreve uma lista de chunks no S3 em formato JSONL.
    
    Args:
        chunks: Lista de chunks a serem escritos
        bucket_name: Nome do bucket S3 de destino
        file_key: Chave do arquivo no bucket S3
        
    Returns:
        URI do arquivo escrito no S3
    """
    s3_client = boto3.client("s3")
    
    # Converte chunks para JSONL
    jsonl_content = chunks_to_jsonl(chunks)
    
    logger.info(f"Escrevendo {len(chunks)} chunks em s3://{bucket_name}/{file_key}")
    
    # Escreve no S3
    s3_client.put_object(
        Bucket=bucket_name,
        Key=file_key,
        Body=jsonl_content.encode("utf-8"),
        ContentType="application/x-jsonlines"
    )
    
    logger.info(f"Arquivo escrito com sucesso: s3://{bucket_name}/{file_key}")
    
    return f"s3://{bucket_name}/{file_key}"