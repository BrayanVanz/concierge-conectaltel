import os
import boto3
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("upload_chunks_to_s3")


def upload_chunks_to_s3(
    bucket_name: str,
    local_chunks_dir: str = "data/chunks",
    s3_prefix: str = "chunks"
) -> None:
    """
    Faz upload dos 3 arquivos de chunks locais para o S3.
    
    Args:
        bucket_name: Nome do bucket S3 de destino
        local_chunks_dir: Diretório local contendo os arquivos de chunks
        s3_prefix: Prefixo no bucket S3 para os arquivos
    """
    s3_client = boto3.client("s3")
    chunks_dir = Path(local_chunks_dir).resolve()
    
    if not chunks_dir.exists():
        logger.error(f"Diretório local não encontrado: {chunks_dir}")
        return
    
    logger.info(f"Fazendo upload de {chunks_dir} para s3://{bucket_name}/{s3_prefix}/")
    
    # Arquivos específicos para upload
    chunk_files = [
        "chunks_fixed_window.jsonl",
        "chunks_full_document.jsonl",
        "chunks_hierarchical_semantic.jsonl"
    ]
    
    uploaded_count = 0
    for chunk_file in chunk_files:
        file_path = chunks_dir / chunk_file
        
        if not file_path.exists():
            logger.warning(f"Arquivo não encontrado: {file_path}")
            continue
        
        s3_key = f"{s3_prefix}/{chunk_file}"
        
        try:
            s3_client.upload_file(
                str(file_path),
                bucket_name,
                s3_key
            )
            logger.info(f"Upload: {chunk_file} -> s3://{bucket_name}/{s3_key}")
            uploaded_count += 1
        except Exception as e:
            logger.error(f"Erro ao fazer upload de {chunk_file}: {e}")
    
    logger.info(f"Upload concluído: {uploaded_count}/{len(chunk_files)} arquivos")


if __name__ == "__main__":
    import sys
    
    # Obtém nome do bucket via variável de ambiente ou argumento de linha de comando
    bucket_name = os.environ.get("PROCESSED_BUCKET_NAME")
    
    if not bucket_name:
        if len(sys.argv) < 2:
            print("Uso: python upload_chunks_to_s3.py <bucket-name>")
            print("Ou defina a variável de ambiente PROCESSED_BUCKET_NAME")
            sys.exit(1)
        bucket_name = sys.argv[1]
    
    # Permite diretório customizado via argumento
    local_chunks_dir = sys.argv[2] if len(sys.argv) > 2 else "data/chunks"
    s3_prefix = sys.argv[3] if len(sys.argv) > 3 else "chunks"
    
    try:
        upload_chunks_to_s3(bucket_name, local_chunks_dir, s3_prefix)
    except Exception as e:
        logger.exception("Erro ao fazer upload para S3")
        sys.exit(1)