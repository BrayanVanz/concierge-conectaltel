"""
Publica o log de auditoria local (`data/audit/audit_log.jsonl`) no bucket
S3 dedicado de auditoria, provisionado pelo Terraform (`terraform/audit.tf`).

A trilha de auditoria (Parte 5) grava localmente em
`data/audit/audit_log.jsonl` a cada resposta do agente (ver
`src/audit/audit_log.py`); este script, rodado à parte, sobe uma cópia
desse arquivo para o S3 como backup/consolidação entre máquinas — não é
obrigatório para o funcionamento da trilha de auditoria em si, que
continua 100% local e consultável em <60s via `src/audit/lookup_cli.py`,
sem depender do S3 ou de qualquer serviço externo.

Pegue o nome do bucket com:

    terraform -chdir=terraform output -raw audit_bucket_name

Uso:

    export AUDIT_BUCKET_NAME="<saída do terraform>"
    python src/audit/upload_audit_log_to_s3.py

    # ou passando o bucket como argumento:
    python src/audit/upload_audit_log_to_s3.py <bucket-name>
"""

import logging
import os
import sys
from pathlib import Path

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("upload_audit_log_to_s3")

DEFAULT_LOCAL_PATH = "data/audit/audit_log.jsonl"
DEFAULT_S3_PREFIX = ""


def upload_audit_log_to_s3(
    bucket_name: str,
    local_path: str = DEFAULT_LOCAL_PATH,
    s3_prefix: str = DEFAULT_S3_PREFIX,
) -> None:
    """
    Sobe o arquivo local de trilha de auditoria para o bucket S3 dedicado
    de auditoria.

    Args:
        bucket_name: nome do bucket de destino (saída `audit_bucket_name`
            do Terraform).
        local_path: caminho do arquivo local de auditoria (padrão:
            `data/audit/audit_log.jsonl`, gerado por `src/audit/audit_log.py`).
        s3_prefix: prefixo opcional dentro do bucket (vazio grava na raiz —
            o bucket já é dedicado a auditoria).
    """
    s3_client = boto3.client("s3")
    audit_file = Path(local_path).resolve()

    if not audit_file.exists():
        logger.warning(
            "Nenhum log de auditoria encontrado em %s. Rode o agente "
            "(`python -m src.agent.cli`) pelo menos uma vez antes.",
            audit_file,
        )
        return

    prefix = s3_prefix.strip("/")
    s3_key = f"{prefix}/{audit_file.name}" if prefix else audit_file.name

    try:
        s3_client.upload_file(str(audit_file), bucket_name, s3_key)
        logger.info(
            "Upload concluído: %s -> s3://%s/%s",
            audit_file,
            bucket_name,
            s3_key,
        )
    except Exception as exc:  # noqa: BLE001 - registra e propaga
        logger.error("Erro ao fazer upload do log de auditoria: %s", exc)
        raise


if __name__ == "__main__":
    bucket = os.environ.get("AUDIT_BUCKET_NAME")

    if not bucket:
        if len(sys.argv) < 2:
            print("Uso: python src/audit/upload_audit_log_to_s3.py <bucket-name>")
            print("Ou defina a variável de ambiente AUDIT_BUCKET_NAME.")
            sys.exit(1)
        bucket = sys.argv[1]

    local_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LOCAL_PATH

    try:
        upload_audit_log_to_s3(bucket, local_file)
    except Exception as exc:  # noqa: BLE001 - barreira final
        logger.exception("Erro ao fazer upload para o S3 | motivo=%s", exc)
        sys.exit(1)