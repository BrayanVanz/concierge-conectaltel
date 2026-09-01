"""Módulo de integração com OpenSearch Serverless para armazenamento e busca vetorial."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

logger = logging.getLogger("vector_store")


class OpenSearchVectorStoreError(Exception):
    """Exceção customizada para erros no OpenSearchVectorStore."""
    pass


def load_embedded_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """Carrega documentos e vetores de um arquivo *_embedded.jsonl."""
    docs = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


class OpenSearchVectorStore:
    def __init__(self, endpoint: str, region: str = "us-east-1", index_name: str = "concierge-vectors"):
        self.endpoint = endpoint.replace("https://", "").rstrip("/")
        self.region = region
        self.index_name = index_name

        credentials = boto3.Session().get_credentials()
        self.awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            self.region,
            "aoss",
            session_token=credentials.token,
        )

        self.client = OpenSearch(
            hosts=[{"host": self.endpoint, "port": 443}],
            http_auth=self.awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=60,             # Aumenta o timeout HTTP de 10s para 60s
            max_retries=5,          # Realiza até 5 tentativas em caso de oscilação
            retry_on_timeout=True
        )

    def ensure_index(self, dimension: int = 1024) -> None:
        """Cria o índice k-NN no OpenSearch se ele ainda não existir."""
        try:
            if not self.client.indices.exists(index=self.index_name):
                index_body = {
                "settings": {
                    "index.knn": True
                },
                "mappings": {
                    "properties": {
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": dimension,
                            "method": {
                                "name": "hnsw",
                                "space_type": "cosinesimil",
                                "engine": "nmslib",
                                "parameters": {
                                    "ef_construction": 128,
                                    "m": 16
                                }
                            }
                        },
                        "status": {"type": "keyword"},
                        "doc_family_id": {"type": "keyword"},
                        "content": {"type": "text"},
                        "source": {"type": "keyword"}
                    }
                }
            }
                self.client.indices.create(index=self.index_name, body=index_body)
                logger.info("Índice criado com sucesso | index=%s | dimension=%d", self.index_name, dimension)
        except Exception as e:
            raise OpenSearchVectorStoreError(f"Erro ao criar/verificar o índice {self.index_name}: {e}")

    def bulk_index(self, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Indexa uma lista de documentos no OpenSearch utilizando a API de Bulk."""
        if not docs:
            return {}

        bulk_data = []
        for doc in docs:
            bulk_data.append(json.dumps({"index": {"_index": self.index_name}}))
            bulk_data.append(json.dumps(doc, ensure_ascii=False))

        body = "\n".join(bulk_data) + "\n"

        try:
            response = self.client.bulk(body=body)
            return response
        except Exception as e:
            raise OpenSearchVectorStoreError(f"Erro no envio em lote para o índice {self.index_name}: {e}")