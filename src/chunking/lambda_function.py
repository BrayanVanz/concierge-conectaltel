import json
import logging
import os
from typing import Any, Dict, List

import boto3

from chunking.reader import read_jsonl_from_s3
from chunking.chunk_strategies import (
    chunk_fixed_window,
    chunk_full_document,
    chunk_hierarchical_semantic
)
from chunking.payload_formatter import format_strategy_payloads

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("chunking_orchestrator")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda Handler para orquestrar o pipeline completo de chunking.
    
    Pipeline:
    1. Lê documentos JSONL do S3 (Passo 1)
    2. Aplica 3 estratégias de chunking (Passo 2)
    3. Padroniza payloads de saída (Passo 3)
    
    Gera 3 listas separadas de payloads em memória (uma por estratégia).
    """
    logger.info(f"Evento recebido: {json.dumps(event)}")
    
    raw_bucket = os.environ.get("RAW_BUCKET_NAME")
    
    # Determina a chave do arquivo a ser processado
    file_key = None
    
    # Se disparado por evento S3
    if "Records" in event and len(event["Records"]) > 0:
        s3_record = event["Records"][0].get("s3", {})
        if "bucket" in s3_record and "object" in s3_record:
            raw_bucket = s3_record["bucket"]["name"]
            file_key = s3_record["object"]["key"]
    # Se disparado manualmente com chave explícita
    elif "file_key" in event:
        file_key = event["file_key"]
    
    if not raw_bucket:
        err_msg = "RAW_BUCKET_NAME environment variable must be set."
        logger.error(err_msg)
        return {"statusCode": 400, "body": json.dumps({"error": err_msg})}
    
    if not file_key:
        err_msg = "file_key must be provided in event or triggered by S3 event."
        logger.error(err_msg)
        return {"statusCode": 400, "body": json.dumps({"error": err_msg})}
    
    try:
        # Passo 1: Lê documentos do S3
        logger.info("Passo 1: Lendo documentos do S3...")
        documents = read_jsonl_from_s3(raw_bucket, file_key)
        logger.info(f"Documentos carregados: {len(documents)}")
        
        # Passo 2: Aplica as 3 estratégias de chunking
        logger.info("Passo 2: Aplicando estratégias de chunking...")
        
        # Estratégia 1: Fixed Window
        logger.info("Aplicando chunk_fixed_window...")
        fixed_window_chunks = chunk_fixed_window(documents, chunk_size=500, overlap=100)
        logger.info(f"Chunks fixed_window: {len(fixed_window_chunks)}")
        
        # Estratégia 2: Full Document
        logger.info("Aplicando chunk_full_document...")
        full_document_chunks = chunk_full_document(documents)
        logger.info(f"Chunks full_document: {len(full_document_chunks)}")
        
        # Estratégia 3: Hierarchical Semantic
        logger.info("Aplicando chunk_hierarchical_semantic...")
        hierarchical_chunks = chunk_hierarchical_semantic(documents)
        logger.info(f"Chunks hierarchical_semantic: {len(hierarchical_chunks)}")
        
        # Passo 3: Padroniza payloads de saída
        logger.info("Passo 3: Padronizando payloads...")
        
        fixed_window_payloads = format_strategy_payloads(fixed_window_chunks, "fixed_window")
        full_document_payloads = format_strategy_payloads(full_document_chunks, "full_document")
        hierarchical_payloads = format_strategy_payloads(hierarchical_chunks, "hierarchical_semantic")
        
        logger.info(f"Payloads padronizados:")
        logger.info(f"  - fixed_window: {len(fixed_window_payloads)}")
        logger.info(f"  - full_document: {len(full_document_payloads)}")
        logger.info(f"  - hierarchical_semantic: {len(hierarchical_payloads)}")
        
        # Retorna estatísticas (os payloads estão em memória para uso futuro)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Chunking pipeline completed successfully",
                "documents_processed": len(documents),
                "chunks_generated": {
                    "fixed_window": len(fixed_window_payloads),
                    "full_document": len(full_document_payloads),
                    "hierarchical_semantic": len(hierarchical_payloads)
                },
                "total_chunks": len(fixed_window_payloads) + len(full_document_payloads) + len(hierarchical_payloads),
                "source": {
                    "bucket": raw_bucket,
                    "file_key": file_key
                }
            })
        }
    except Exception as e:
        logger.exception("Erro ao executar pipeline de chunking")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }