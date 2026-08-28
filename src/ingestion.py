import argparse
import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ingestion")


def parse_frontmatter(file_content: str) -> Tuple[Dict[str, Any], str]:
    """
    Extracts YAML frontmatter metadata and content from markdown text.
    Preserves all content after frontmatter, including Markdown headers.
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n"
    match = re.match(pattern, file_content, re.DOTALL)
    if match:
        yaml_text = match.group(1)
        try:
            metadata = yaml.safe_load(yaml_text) or {}
        except Exception as e:
            logger.warning(f"Error parsing YAML frontmatter: {e}")
            metadata = {}
        content = file_content[match.end():]
        return metadata, content
    return {}, file_content


def _normalize_value(val: Any) -> Any:
    """Converts dates or non-serializable objects into JSON-friendly formats."""
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    return val


def parse_document(file_path: Path, base_dir: Path, doc_id: int) -> Dict[str, Any]:
    """
    Reads a markdown document, extracts frontmatter, and formats the output record.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        file_content = f.read()

    frontmatter, content = parse_frontmatter(file_content)

    # Compute relative path and category
    rel_path = file_path.relative_to(base_dir)
    category = rel_path.parent.as_posix() if rel_path.parent != Path(".") else ""

    # Normalize frontmatter metadata and remove doc_family_id
    normalized_meta = {k: _normalize_value(v) for k, v in frontmatter.items()}
    normalized_meta.pop("doc_family_id", None)
    normalized_meta["category"] = category

    record = {
        "id": doc_id,
        "source": file_path.name,
        "metadata": normalized_meta,
        "content": content.strip()
    }

    return record


def ingest_corpus(input_dir: str | Path, output_file: str | Path) -> List[Dict[str, Any]]:
    """
    Ingests all markdown documents in input_dir (excluding log_chamados)
    and saves the extracted records into a JSONL output file.
    """
    input_path = Path(input_dir)
    output_path = Path(output_file)

    if not input_path.exists():
        raise FileNotFoundError(f"Input directory not found: {input_path}")

    # Find all markdown files recursively
    md_files = sorted(list(input_path.rglob("*.md")))
    records = []
    current_id = 1

    for file_path in md_files:
        # Exclude log_chamados directory or files
        if "log_chamados" in file_path.parts or "log_chamados" in file_path.name:
            logger.info(f"Skipping excluded file/dir: {file_path}")
            continue

        try:
            record = parse_document(file_path, input_path, doc_id=current_id)
            records.append(record)
            logger.info(f"Ingested document ID {record['id']}: {record['source']}")
            current_id += 1
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write records to JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(f"Successfully wrote {len(records)} document records to {output_path}")
    return records


def main():
    parser = argparse.ArgumentParser(description="Ingest RAG corpus markdown data into JSONL format.")
    parser.add_argument(
        "--input-dir",
        type=str,
        default="data/raw/corpus",
        help="Path to corpus directory containing raw markdown files."
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="data/processed/corpus.jsonl",
        help="Path to output JSONL file."
    )

    args = parser.parse_args()
    ingest_corpus(args.input_dir, args.output_file)


if __name__ == "__main__":
    main()
