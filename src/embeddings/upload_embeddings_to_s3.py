"""
Publica os arquivos vetorizados (`*_embedded.jsonl`) no bucket S3 de
embeddings criado pelo Terraform.

Segue o mesmo padrão de `src/chunking/upload_chunks_to_s3.py`: o `main.py`
grava a saída em `data/embeddings/` no disco local; este script, rodado
depois, sobe esses arquivos para o bucket S3 compartilhado, de onde as
etapas seguintes do pipeline (busca / índice vetorial) leem.

O bucket é provisionado pelo Terraform (`terraform/s3.tf`). Pegue o nome
com:

    terraform -chdir=terraform output -raw embeddings_bucket_name

Uso:

    export EMBEDDINGS_BUCKET_NAME="<saída do terraform>"
    python src/embeddings/upload_embeddings_to_s3.py

    # ou passando o bucket como argumento:
    python src/embeddings/upload_embeddings_to_s3.py <bucket-name>
"""

import logging
import os
import sys
from pathlib import Path

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("upload_embeddings_to_s3")


def upload_embeddings_to_s3(
    bucket_name: str,
    local_embeddings_dir: str = "data/embeddings",
    s3_prefix: str = "",
) -> None:
    """
    Sobe todos os arquivos `*_embedded.jsonl` de um diretório local para o
    bucket S3 de embeddings.

    Args:
        bucket_name: nome do bucket de destino (saída `embeddings_bucket_name`
            do Terraform).
        local_embeddings_dir: diretório local com os arquivos vetorizados
            (padrão: `data/embeddings`, a saída do `main.py`).
        s3_prefix: prefixo opcional dentro do bucket. Vazio grava na raiz do
            bucket — o bucket já é dedicado a embeddings.
    """
    s3_client = boto3.client("s3")
    embeddings_dir = Path(local_embeddings_dir).resolve()

    if not embeddings_dir.exists():
        logger.error("Diretório local não encontrado: %s", embeddings_dir)
        return

    files = sorted(embeddings_dir.glob("*_embedded.jsonl"))
    if not files:
        logger.warning(
            "Nenhum arquivo *_embedded.jsonl em %s. Rode "
            "`python src/embeddings/main.py` antes.",
            embeddings_dir,
        )
        return

    prefix = s3_prefix.strip("/")
    logger.info(
        "Fazendo upload de %d arquivo(s) de %s para s3://%s/%s",
        len(files),
        embeddings_dir,
        bucket_name,
        prefix,
    )

    uploaded_count = 0
    for file_path in files:
        s3_key = f"{prefix}/{file_path.name}" if prefix else file_path.name
        try:
            s3_client.upload_file(str(file_path), bucket_name, s3_key)
            logger.info("Upload: %s -> s3://%s/%s", file_path.name, bucket_name, s3_key)
            uploaded_count += 1
        except Exception as exc:  # noqa: BLE001 - registra e segue para os demais
            logger.error("Erro ao fazer upload de %s: %s", file_path.name, exc)

    logger.info("Upload concluído: %d/%d arquivos", uploaded_count, len(files))


if __name__ == "__main__":
    bucket = os.environ.get("EMBEDDINGS_BUCKET_NAME")

    if not bucket:
        if len(sys.argv) < 2:
            print("Uso: python src/embeddings/upload_embeddings_to_s3.py <bucket-name>")
            print("Ou defina a variável de ambiente EMBEDDINGS_BUCKET_NAME.")
            sys.exit(1)
        bucket = sys.argv[1]

    local_dir = sys.argv[2] if len(sys.argv) > 2 else "data/embeddings"

    try:
        upload_embeddings_to_s3(bucket, local_dir)
    except Exception as exc:  # noqa: BLE001 - barreira final
        logger.exception("Erro ao fazer upload para o S3 | motivo=%s", exc)
        sys.exit(1)
