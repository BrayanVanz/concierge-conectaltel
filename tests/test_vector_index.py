import json
import tempfile
import unittest
from pathlib import Path

from src.vector_index.indexer import VectorIndex


class VectorIndexTest(unittest.TestCase):
    def test_build_and_search_filters_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            embedded_path = Path(tmpdir) / "sample_embedded.jsonl"
            records = [
                {
                    "chunk_id": "vigente-1",
                    "text_content": "resposta vigente",
                    "metadata": {"status": "vigente", "doc_family_id": "familia-a"},
                    "embedding": [1.0, 0.0, 0.0],
                },
                {
                    "chunk_id": "revogado-1",
                    "text_content": "resposta revogada",
                    "metadata": {"status": "revogado", "doc_family_id": "familia-a"},
                    "embedding": [0.9, 0.0, 0.0],
                },
                {
                    "chunk_id": "vigente-2",
                    "text_content": "outra resposta vigente",
                    "metadata": {"status": "vigente", "doc_family_id": "familia-b"},
                    "embedding": [0.0, 1.0, 0.0],
                },
            ]

            with embedded_path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            index = VectorIndex.from_jsonl(embedded_path)
            hits = index.search(
                [1.0, 0.0, 0.0],
                top_k=2,
                status_filter="vigente",
                doc_family_id="familia-a",
            )

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["chunk_id"], "vigente-1")
            self.assertEqual(hits[0]["metadata"]["status"], "vigente")


if __name__ == "__main__":
    unittest.main()
