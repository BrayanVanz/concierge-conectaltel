import json
import logging
import os
from typing import Any, Dict, List

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("chunking_step1")


def read_jsonl_from_s3(bucket_name: str, file_key: str) -> List[Dict[str, Any]]:
    """
    Lê um arquivo .jsonl do S3 e carrega o conteúdo parseado em memória.
    
    Args:
        bucket_name: Nome do bucket S3
        file_key: Chave do arquivo .jsonl no bucket
        
    Returns:
        Lista de dicionários parseados do JSONL
    """
    s3_client = boto3.client("s3")
    
    logger.info(f"Lendo arquivo do S3: s3://{bucket_name}/{file_key}")
    
    # Lê o objeto do S3
    response = s3_client.get_object(
        Bucket=bucket_name,
        Key=file_key
    )
    
    # Decodifica o conteúdo
    file_content = response["Body"].read().decode("utf-8")
    logger.info(f"Arquivo lido: {len(file_content)} caracteres")
    
    # Parse cada linha do JSONL
    documents = []
    for line_num, line in enumerate(file_content.splitlines(), 1):
        if line.strip():  # Ignora linhas vazias
            try:
                doc = json.loads(line)
                documents.append(doc)
            except json.JSONDecodeError as e:
                logger.error(f"Erro ao parsear linha {line_num}: {e}")
    
    logger.info(f"Total de registros carregados: {len(documents)}")
    
    return documents


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda Handler para ler JSONL do S3.
    Pode ser disparado via evento S3 ou manualmente com a chave do arquivo.
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
        documents = read_jsonl_from_s3(raw_bucket, file_key)
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Successfully loaded {len(documents)} records from JSONL.",
                "bucket": raw_bucket,
                "file_key": file_key,
                "record_count": len(documents)
            })
        }
    except Exception as e:
        logger.exception("Erro ao executar leitura do JSONL")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }