"""
AWS Lambda handler da etapa de embeddings do Concierge ConectaTel.

Espelha `src/chunking/lambda_function.py`: é disparado por um evento S3
(quando um `chunks/*.jsonl` aparece no bucket `processed`) ou por invocação
manual com `file_key`. Para cada arquivo de chunks, gera os embeddings via
Bedrock e grava `<nome>_embedded.jsonl` no bucket de embeddings.

Fluxo:
    1. Descobre a chave do arquivo de chunks (do evento S3 ou do payload).
    2. Baixa o JSONL do S3.
    3. `Embedder.embed_chunks()` — mesma lógica que o `main.py` local usa.
    4. Grava o JSONL enriquecido no bucket de saída.

Variáveis de ambiente (definidas em `terraform/lambda.tf`):
    INPUT_BUCKET_NAME    bucket de onde ler os chunks (o `processed`)
    OUTPUT_BUCKET_NAME   bucket onde gravar os embeddings
    INPUT_PREFIX         prefixo dos chunks (padrão "chunks/")
    OUTPUT_PREFIX        prefixo da saída (padrão "" = raiz do bucket)

O runner local equivalente é `src/embeddings/main.py`.
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3

from embedder import Embedder, EmbedderConfig, EmbedderError, embedded_output_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("embeddings_lambda")

_s3 = boto3.client("s3")


def _resolve_file_key(event: Dict[str, Any]) -> str:
    """
    Descobre qual arquivo de chunks processar:

    - Disparo por evento S3 → `Records[0].s3.object.key`.
    - Invocação manual → campo `file_key` no payload.

    Returns:
        A chave (caminho no bucket) do arquivo, ou "" se não houver.
    """
    records = event.get("Records") or []
    if records:
        s3_info = records[0].get("s3", {})
        key = s3_info.get("object", {}).get("key")
        if key:
            return key
    return event.get("file_key", "")


def _read_jsonl_from_s3(bucket: str, key: str) -> List[Dict[str, Any]]:
    """Baixa um JSONL do S3 e devolve a lista de objetos, pulando linhas em branco."""
    body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logger.info("Evento recebido: %s", json.dumps(event))

    input_bucket = os.environ.get("INPUT_BUCKET_NAME")
    output_bucket = os.environ.get("OUTPUT_BUCKET_NAME")
    output_prefix = os.environ.get("OUTPUT_PREFIX", "").strip("/")

    # Se o evento S3 trouxer o bucket, ele tem prioridade sobre a env var.
    records = event.get("Records") or []
    if records:
        evt_bucket = records[0].get("s3", {}).get("bucket", {}).get("name")
        if evt_bucket:
            input_bucket = evt_bucket

    if not input_bucket or not output_bucket:
        msg = "INPUT_BUCKET_NAME e OUTPUT_BUCKET_NAME são obrigatórias."
        logger.error(msg)
        return {"statusCode": 400, "body": json.dumps({"error": msg})}

    file_key = _resolve_file_key(event)
    if not file_key:
        msg = "Nenhum arquivo para processar (nem evento S3 nem 'file_key' no payload)."
        logger.error(msg)
        return {"statusCode": 400, "body": json.dumps({"error": msg})}

    try:
        input_uri = f"s3://{input_bucket}/{file_key}"
        logger.info("Lendo chunks | uri=%s | motivo=início", input_uri)
        chunks = _read_jsonl_from_s3(input_bucket, file_key)
        logger.info("Chunks lidos | quantidade=%d", len(chunks))

        # Mesma lógica de embedding do main.py local — só o I/O muda.
        embedder = Embedder(EmbedderConfig(aws_region=os.environ.get("AWS_REGION", "us-east-1")))
        records_out = embedder.embed_chunks(chunks)

        # chunks/chunks_x.jsonl  ->  chunks_x_embedded.jsonl (só o nome, sem diretório)
        out_name = embedded_output_path(file_key).name
        out_key = f"{output_prefix}/{out_name}" if output_prefix else out_name

        jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in records_out) + "\n"
        _s3.put_object(
            Bucket=output_bucket,
            Key=out_key,
            Body=jsonl.encode("utf-8"),
            ContentType="application/x-jsonlines",
        )
        output_uri = f"s3://{output_bucket}/{out_key}"
        logger.info(
            "Embeddings gravados | uri=%s | registros=%d | ignorados=%d | motivo=fim",
            output_uri,
            len(records_out),
            embedder.stats.total_chunks_skipped_invalid,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Embeddings gerados com sucesso.",
                    "input": input_uri,
                    "output": output_uri,
                    "chunks_lidos": embedder.stats.total_chunks_read,
                    "chunks_embedados": embedder.stats.total_chunks_embedded,
                    "chunks_ignorados": embedder.stats.total_chunks_skipped_invalid,
                }
            ),
        }
    except EmbedderError as exc:
        logger.exception("Falha do Embedder")
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
    except Exception as exc:  # noqa: BLE001 - barreira final
        logger.exception("Erro inesperado no handler de embeddings")
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
