import os
import boto3
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("upload")

def upload_corpus_to_s3(bucket_name: str, local_path: str = "../data/raw/corpus", s3_prefix: str = "corpus"):
    """
    Faz upload de todos os arquivos .md do corpus local para o S3
    """
    s3_client = boto3.client("s3")
    local_dir = Path(local_path).resolve()
    
    if not local_dir.exists():
        logger.error(f"Diretório local não encontrado: {local_dir}")
        return
    
    logger.info(f"Fazendo upload de {local_dir} para s3://{bucket_name}/{s3_prefix}/")
    
    md_files = list(local_dir.rglob("*.md"))
    logger.info(f"Encontrados {len(md_files)} arquivos .md")
    
    uploaded_count = 0
    for md_file in md_files:
        relative_path = md_file.relative_to(local_dir)
        s3_key = f"{s3_prefix}/{relative_path.as_posix()}"
        
        try:
            s3_client.upload_file(
                str(md_file),
                bucket_name,
                s3_key
            )
            logger.info(f"Upload: {md_file.name} -> s3://{bucket_name}/{s3_key}")
            uploaded_count += 1
        except Exception as e:
            logger.error(f"Erro ao fazer upload de {md_file}: {e}")
    
    logger.info(f"Upload concluído: {uploaded_count}/{len(md_files)} arquivos")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Uso: python upload_to_s3.py <bucket-name>")
        sys.exit(1)
    
    bucket_name = sys.argv[1]
    upload_corpus_to_s3(bucket_name)