import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ingestion")


def parse_frontmatter(file_content: str) -> Tuple[Dict[str, Any], str]:
    """
    Extracts simple frontmatter metadata and content from Markdown.
    Does not require PyYAML.
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n"
    match = re.match(pattern, file_content, re.DOTALL)

    if not match:
        return {}, file_content

    frontmatter_text = match.group(1)
    metadata = {}

    for line in frontmatter_text.splitlines():
        line = line.strip()

        if not line or ":" not in line:
            continue

        key, value = line.split(":", 1)

        key = key.strip()
        value = value.strip()

        # Empty YAML value -> None
        if value == "":
            value = None

        # Remove surrounding quotes
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        # Convert integer values
        elif value.isdigit():
            value = int(value)

        metadata[key] = value

    content = file_content[match.end():]

    return metadata, content


def _normalize_value(val: Any) -> Any:
    """Converts dates or non-serializable objects into JSON-friendly formats."""
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    return val


def parse_document_content(
    file_content: str,
    source_name: str,
    category: str,
    doc_id: int
) -> Dict[str, Any]:

    frontmatter, content = parse_frontmatter(file_content)

    normalized_meta = {
        k: _normalize_value(v)
        for k, v in frontmatter.items()
    }

    normalized_meta["category"] = category

    # Extrair campos específicos para o nível superior do documento
    doc_family_id = normalized_meta.pop("doc_family_id", f"doc_{doc_id}")
    version_ordinal = normalized_meta.pop("version_ordinal", 1)
    effective_from = normalized_meta.pop("effective_from", "")
    effective_to = normalized_meta.pop("effective_to", "")
    status = normalized_meta.pop("status", "vigente")
    title = content.split('\n')[0].replace('#', '').strip() if content.strip().startswith('#') else source_name

    record = {
        "id": doc_id,
        "source": source_name,
        "doc_family_id": doc_family_id,
        "version_ordinal": version_ordinal,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "status": status,
        "title": title,
        "metadata": normalized_meta,
        "content": content.strip()
    }

    return record


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """
    Parses s3://bucket-name/key/path into (bucket, key).
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Invalid S3 URI scheme: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def ingest_s3_corpus(
    input_s3_uri: str,
    output_s3_uri: str
) -> List[Dict[str, Any]]:

    s3_client = boto3.client("s3")

    in_bucket, in_prefix = parse_s3_uri(input_s3_uri)
    out_bucket, out_key = parse_s3_uri(output_s3_uri)

    logger.info(f"Scanning bucket: {in_bucket}")
    logger.info(f"Scanning prefix: {in_prefix}")

    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=in_bucket,
        Prefix=in_prefix
    )

    records = []
    current_id = 1
    s3_keys = []

    # Find Markdown files
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]

            logger.info(f"S3 object found: {key}")

            if key.lower().endswith(".md"):
                s3_keys.append(key)

    logger.info(f"Markdown files found: {len(s3_keys)}")
    logger.info(f"Markdown keys: {s3_keys}")

    s3_keys.sort()

    for key in s3_keys:

        if Path(key).stem == "log_chamados":
            logger.info(f"Skipping excluded S3 object: {key}")
            continue

        try:
            logger.info(f"Downloading: {key}")

            response = s3_client.get_object(
                Bucket=in_bucket,
                Key=key
            )

            file_content = response["Body"].read().decode("utf-8")

            logger.info(
                f"Downloaded {key}: {len(file_content)} characters"
            )

            source_name = Path(key).name

            if in_prefix and key.startswith(in_prefix):
                rel_key = key[len(in_prefix):].lstrip("/")
            else:
                rel_key = key

            parent = Path(rel_key).parent.as_posix()
            category = parent if parent != "." else ""

            record = parse_document_content(
                file_content,
                source_name,
                category,
                current_id
            )

            records.append(record)

            logger.info(
                f"Ingested document {record['id']}: {key}"
            )

            current_id += 1

        except Exception as e:
            logger.exception(
                f"Error processing S3 key {key}"
            )

    logger.info(f"Total records generated: {len(records)}")

    jsonl_content = "\n".join(
        json.dumps(r, ensure_ascii=False)
        for r in records
    )

    if records:
        jsonl_content += "\n"

    logger.info(
        f"Uploading {len(records)} records to "
        f"s3://{out_bucket}/{out_key}"
    )

    s3_client.put_object(
        Bucket=out_bucket,
        Key=out_key,
        Body=jsonl_content.encode("utf-8"),
        ContentType="application/x-jsonlines"
    )

    logger.info(
        f"Successfully uploaded JSONL output to "
        f"s3://{out_bucket}/{out_key}"
    )

    return records


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda Function Handler triggered by S3 ObjectCreated events or direct invocations.
    """
    logger.info(f"Received Lambda event: {json.dumps(event)}")

    raw_bucket = os.environ.get("RAW_BUCKET_NAME")
    processed_bucket = os.environ.get("PROCESSED_BUCKET_NAME")
    output_key = os.environ.get("OUTPUT_KEY", "corpus.jsonl")
    input_prefix = os.environ.get("INPUT_PREFIX", "corpus/")

    # If triggered by S3 event, override raw_bucket from event if available
    if "Records" in event and len(event["Records"]) > 0:
        s3_record = event["Records"][0].get("s3", {})
        if "bucket" in s3_record and "name" in s3_record["bucket"]:
            raw_bucket = s3_record["bucket"]["name"]

    if not raw_bucket or not processed_bucket:
        err_msg = "RAW_BUCKET_NAME and PROCESSED_BUCKET_NAME environment variables must be set."
        logger.error(err_msg)
        return {"statusCode": 400, "body": json.dumps({"error": err_msg})}

    input_uri = f"s3://{raw_bucket}/{input_prefix.lstrip('/')}"
    output_uri = f"s3://{processed_bucket}/{output_key.lstrip('/')}"

    try:
        records = ingest_s3_corpus(input_uri, output_uri)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Successfully ingested {len(records)} documents.",
                "input_uri": input_uri,
                "output_uri": output_uri
            })
        }
    except Exception as e:
        logger.exception("Error executing ingestion in Lambda handler")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
