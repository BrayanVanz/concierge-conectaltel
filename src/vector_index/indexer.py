import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import numpy as np


class VectorIndexError(ValueError):
    """Erro específico do índice vetorial."""


class VectorIndex:
    """Índice local em memória para busca vetorial por similaridade coseno.

    O projeto já produz arquivos JSONL com embeddings no formato:
        {"chunk_id": ..., "text_content": ..., "metadata": {...}, "embedding": [...]}

    Este componente apenas carrega esses registros, normaliza os vetores,
    constrói um índice rápido em RAM e executa busca por similaridade.
    A escolha é deliberada: o corpus deste desafio é pequeno e estático,
    então um índice em memória é mais simples, previsível e robusto do que
    depender de um serviço externo para a fase de recuperação local.
    """

    def __init__(self, records: Iterable[Dict[str, Any]]):
        self.records = list(records)
        if not self.records:
            raise VectorIndexError("Nenhum registro encontrado para indexação.")

        self.vectors = np.vstack([
            self._to_numpy_vector(record["embedding"])
            for record in self.records
        ]).astype(np.float32)

        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._normalized_vectors = self.vectors / norms

    @staticmethod
    def _to_numpy_vector(value: Sequence[float]) -> np.ndarray:
        vector = np.asarray(value, dtype=np.float32)
        if vector.ndim != 1:
            raise VectorIndexError("Cada embedding deve ser um vetor unidimensional.")
        if vector.size == 0:
            raise VectorIndexError("Embedding vazio não é permitido.")
        return vector

    @classmethod
    def from_jsonl(cls, path: Union[str, Path]) -> "VectorIndex":
        file_path = Path(path)
        if not file_path.exists():
            raise VectorIndexError(f"Arquivo de embeddings não encontrado: {file_path}")

        records: List[Dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise VectorIndexError(
                        f"JSON inválido em {file_path}:{line_number}: {exc}"
                    ) from exc

                if "embedding" not in record:
                    raise VectorIndexError(
                        f"Registro sem campo 'embedding' em {file_path}:{line_number}"
                    )
                records.append(record)

        return cls(records)

    def _matches_filter(
        self,
        record: Dict[str, Any],
        status_filter: Optional[str] = None,
        doc_family_id: Optional[str] = None,
    ) -> bool:
        metadata = record.get("metadata", {}) or {}

        if status_filter is not None:
            record_status = metadata.get("status")
            if record_status is None or str(record_status).lower() != str(status_filter).lower():
                return False

        if doc_family_id is not None:
            record_family_id = metadata.get("doc_family_id")
            if record_family_id is None or str(record_family_id) != str(doc_family_id):
                return False

        return True

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        status_filter: Optional[str] = "vigente",
        doc_family_id: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Busca por similaridade coseno com filtos opcionais.

        Retorno:
            Lista de dicionários com os campos do chunk original mais:
                - score: valor de similaridade coseno
                - rank: posição no ranking
        """
        if top_k <= 0:
            raise VectorIndexError("top_k deve ser maior que zero.")

        query = self._to_numpy_vector(query_vector)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            raise VectorIndexError("Query vector não pode ser nulo.")

        query_normalized = query / query_norm
        scores = self._normalized_vectors.dot(query_normalized)

        candidates = []
        for index, record in enumerate(self.records):
            if not self._matches_filter(record, status_filter=status_filter, doc_family_id=doc_family_id):
                continue
            candidates.append((index, float(scores[index])))

        if not candidates:
            return []

        candidates.sort(key=lambda item: item[1], reverse=True)

        result: List[Dict[str, Any]] = []
        for rank, (index, score) in enumerate(candidates[:top_k], start=1):
            if score_threshold is not None and score < score_threshold:
                continue

            record = dict(self.records[index])
            record["score"] = score
            record["rank"] = rank
            result.append(record)

        return result


__all__ = ["VectorIndex", "VectorIndexError"]
