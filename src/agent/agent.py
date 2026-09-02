import os
import json
import subprocess
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

from src.audit.audit_log import log_trace

class ConciergeAgent:
    def __init__(
        self, 
        opensearch_endpoint: str = None,
        chunk_strategy: str = "hierarchical_semantic", 
        score_threshold: float = 0.30
    ):
        # Resolve o endpoint via argumento, env var ou leitura direta do Terraform
        self.endpoint = opensearch_endpoint or os.getenv("OPENSEARCH_ENDPOINT", "")
        if not self.endpoint:
            try:
                cmd = ["terraform", "-chdir=terraform", "output", "-raw", "opensearch_collection_endpoint"]
                self.endpoint = subprocess.check_output(cmd, text=True).strip()
            except Exception:
                self.endpoint = ""

        self.chunk_strategy = chunk_strategy
        self.score_threshold = score_threshold
        
        # Resolve o ID e Versão do Bedrock Guardrail via Env Var ou fallback automático do Terraform
        self.guardrail_id = os.getenv("BEDROCK_GUARDRAIL_ID", "")
        self.guardrail_version = os.getenv("BEDROCK_GUARDRAIL_VERSION", "1")
        
        if not self.guardrail_id:
            try:
                cmd_gid = ["terraform", "-chdir=terraform", "output", "-raw", "bedrock_guardrail_id"]
                self.guardrail_id = subprocess.check_output(cmd_gid, text=True).strip()
                
                cmd_gver = ["terraform", "-chdir=terraform", "output", "-raw", "bedrock_guardrail_version"]
                self.guardrail_version = subprocess.check_output(cmd_gver, text=True).strip() or "1"
            except Exception:
                self.guardrail_id = ""
                self.guardrail_version = "1"
        
        self.bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

        # Conexão OpenSearch Serverless
        if self.endpoint:
            credentials = boto3.Session().get_credentials()
            awsauth = AWS4Auth(
                credentials.access_key, 
                credentials.secret_key, 
                'us-east-1', 
                'aoss', 
                session_token=credentials.token
            )
            self.opensearch = OpenSearch(
                hosts=[{'host': self.endpoint.replace("https://", "").rstrip("/"), 'port': 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True
            )
        else:
            self.opensearch = None

    def _generate_query_embedding(self, query_text: str) -> list[float]:
        """Gera o embedding da pergunta usando o Cohere Embed v4 no Bedrock."""
        body = json.dumps({
            "texts": [query_text],
            "input_type": "search_query"
        })
        
        response = self.bedrock.invoke_model(
            modelId="cohere.embed-v4:0",
            body=body
        )
        response_body = json.loads(response['body'].read())
        
        embeddings_data = response_body.get('embeddings')
        if isinstance(embeddings_data, dict) and 'float' in embeddings_data:
            return embeddings_data['float'][0]
        return embeddings_data[0]

    def _resolve_index_name(self) -> str:
        """Mapeia a estratégia de chunking para o nome exato do índice no OpenSearch."""
        base_prefix = os.getenv("OPENSEARCH_INDEX_NAME", "concierge-vectors")
        strategy = self.chunk_strategy.replace("chunks_", "").strip()

        mapping = {
            "fixed_window": f"{base_prefix}-fixed-windows",
            "fixed_windows": f"{base_prefix}-fixed-windows",
            "default": f"{base_prefix}-default",
            "full_document": f"{base_prefix}-full-document",
            "hierarchical_semantic": f"{base_prefix}-hierarchical-semantic",
        }

        return mapping.get(strategy, f"{base_prefix}-full-document")

    def retrieve_vigente_chunks(self, query: str, top_k: int = 3):
        if not self.opensearch:
            print("[WARN] Cliente OpenSearch não inicializado. Verifique a variável OPENSEARCH_ENDPOINT.")
            return []

        try:
            target_index = self._resolve_index_name()
            query_vector = self._generate_query_embedding(query)

            search_query = {
                "size": top_k,
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_vector,
                            "k": top_k
                        }
                    }
                }
            }

            response = self.opensearch.search(index=target_index, body=search_query)
            results = []
            for hit in response['hits']['hits']:
                source_data = hit['_source']
                
                doc_status = source_data.get('status', 'vigente')
                if doc_status != 'vigente':
                    continue

                results.append({
                    "score": hit['_score'],
                    "doc_id": source_data.get('doc_family_id') or source_data.get('id'),
                    "content": source_data.get('content') or source_data.get('text_content', ''),
                    "source_ref": source_data.get('source') or source_data.get('source_file', 'documento_oficial')
                })
            return results
        except Exception as e:
            print(f"Erro ao consultar OpenSearch ({self._resolve_index_name()}): {e}")
            return []

    def process_message(self, user_id: str, query: str, conversation_history: list = None):
        conversation_history = conversation_history or []
        guardrail_blocked_msg = "Como assistente da ConectaTel, só posso responder a dúvidas sobre nossos planos, faturas e serviços oficiais."

        # 1. Triagem e Regra de Escalonamento Humano
        if any(k in query.lower() for k in ["contestação", "fatura indevida", "cancelamento"]):
            handoff = {
                "user_id": user_id,
                "categoria": "Financeiro/Retenção",
                "urgencia": "Alta",
                "problema_relatado": query,
                "verificacoes_bot": conversation_history
            }
            resp = "Entendo a situação. Estou encaminhando seu caso para um atendente humano especializado."
            trace_id = log_trace(
                pergunta=query,
                user_id=user_id,
                decisao="ESCALATED",
                guardrail_acionado="regra_de_escalonamento",
                chunk_strategy=self.chunk_strategy,
                resposta=resp,
                extra={"handoff": handoff},
            )
            return {"response": resp, "status": "ESCALATED", "handoff": handoff, "trace_id": trace_id}

        # 2. Guardrail de Entrada (Intercepta Prompt Injection e Fora de Escopo ANTES do RAG)
        if self.guardrail_id:
            try:
                guard_res = self.bedrock.apply_guardrail(
                    guardrailIdentifier=self.guardrail_id,
                    guardrailVersion=self.guardrail_version,
                    source="INPUT",
                    content=[{"text": {"text": query}}]
                )
                if guard_res.get("action") == "GUARDRAIL_INTERVENED":
                    trace_id = log_trace(
                        pergunta=query,
                        user_id=user_id,
                        decisao="BLOCKED_GUARDRAIL",
                        guardrail_acionado="bedrock_guardrail_input",
                        chunk_strategy=self.chunk_strategy,
                        resposta=guardrail_blocked_msg,
                    )
                    return {
                        "response": guardrail_blocked_msg,
                        "status": "BLOCKED_GUARDRAIL",
                        "trace_id": trace_id,
                    }
            except Exception as e:
                if "Guardrail" in str(e) or "intervened" in str(e).lower():
                    trace_id = log_trace(
                        pergunta=query,
                        user_id=user_id,
                        decisao="BLOCKED_GUARDRAIL",
                        guardrail_acionado="bedrock_guardrail_input",
                        chunk_strategy=self.chunk_strategy,
                        resposta=guardrail_blocked_msg,
                    )
                    return {
                        "response": guardrail_blocked_msg,
                        "status": "BLOCKED_GUARDRAIL",
                        "trace_id": trace_id,
                    }

        # 3. Busca RAG no OpenSearch
        chunks = self.retrieve_vigente_chunks(query)
        max_score = max([c['score'] for c in chunks]) if chunks else 0.0

        # 4. Validação do Limiar de Relevância (Para perguntas válidas mas sem dados na base)
        if max_score < self.score_threshold or not chunks:
            no_know_msg = "Não encontrei informações suficientes na documentação oficial da ConectaTel para responder à sua solicitação."
            trace_id = log_trace(
                pergunta=query,
                user_id=user_id,
                decisao="NO_KNOWLEDGE",
                score_max=max_score,
                guardrail_acionado="limiar_de_score",
                chunk_strategy=self.chunk_strategy,
                resposta=no_know_msg,
            )
            return {"response": no_know_msg, "status": "NO_KNOWLEDGE", "trace_id": trace_id}

        # 5. Geração LLM via Bedrock com Guardrail de Saída
        context = "\n\n".join([f"Fonte [{c['source_ref']}]: {c['content']}" for c in chunks])
        prompt = (
            "Responda apenas com base no contexto abaixo. Se não souber, diga que não sabe. "
            "NUNCA inclua nomes de arquivo nem uma seção de 'Fontes' na sua resposta\n\n"
            f"Contexto:\n{context}\n\nPergunta: {query}"
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}]
        })

        invoke_kwargs = {
            "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "body": body
        }

        if self.guardrail_id:
            invoke_kwargs["guardrailIdentifier"] = self.guardrail_id
            invoke_kwargs["guardrailVersion"] = self.guardrail_version

        try:
            res = self.bedrock.invoke_model(**invoke_kwargs)

            headers = res.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            guardrail_action = (
                res.get("amazon-bedrock-guardrailAction") or 
                headers.get("amazon-bedrock-guardrailaction", "")
            ).upper()

            raw_answer = json.loads(res['body'].read())['content'][0]['text']

            if guardrail_action == "INTERVENED" or guardrail_blocked_msg.lower() in raw_answer.lower():
                trace_id = log_trace(
                    pergunta=query,
                    user_id=user_id,
                    decisao="BLOCKED_GUARDRAIL",
                    score_max=max_score,
                    fontes=[c['source_ref'] for c in chunks],
                    guardrail_acionado="bedrock_guardrail_output",
                    chunk_strategy=self.chunk_strategy,
                    resposta=guardrail_blocked_msg,
                )
                return {
                    "response": guardrail_blocked_msg,
                    "status": "BLOCKED_GUARDRAIL",
                    "trace_id": trace_id,
                }

        except Exception as e:
            if "Guardrail" in str(e) or "intervened" in str(e).lower():
                trace_id = log_trace(
                    pergunta=query,
                    user_id=user_id,
                    decisao="BLOCKED_GUARDRAIL",
                    score_max=max_score,
                    fontes=[c['source_ref'] for c in chunks],
                    guardrail_acionado="bedrock_guardrail_output",
                    chunk_strategy=self.chunk_strategy,
                    resposta=guardrail_blocked_msg,
                )
                return {
                    "response": guardrail_blocked_msg,
                    "status": "BLOCKED_GUARDRAIL",
                    "trace_id": trace_id,
                }
            raise e

        # 6. Pós-processamento de Saída e Fontes
        sources = list(set([c['source_ref'] for c in chunks]))

        trace_id = log_trace(
            pergunta=query,
            user_id=user_id,
            decisao="ANSWERED",
            score_max=max_score,
            fontes=sources,
            chunk_strategy=self.chunk_strategy,
            resposta=raw_answer,
        )

        return {"response": raw_answer, "status": "ANSWERED", "sources": sources, "trace_id": trace_id}