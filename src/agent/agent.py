import os
import re
import json
import uuid
import subprocess
from datetime import datetime, timezone
from typing import Optional

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

from src.audit.audit_log import log_trace
from src.agent.escalation_policy import EscalationPolicy

# Nomes de plano usados para tentar identificar "produto_servico_envolvido"
# no registro de escalonamento sem depender de um NER dedicado.
_PRODUTOS_CONHECIDOS = ["conecta básico", "conecta basico", "conecta plus", "conecta família", "conecta familia"]


class ConciergeAgent:
    def __init__(
        self,
        opensearch_endpoint: str = None,
        chunk_strategy: str = "hierarchical_semantic",
        score_threshold: float = 0.72,
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

        self.bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

        # Política de escalonamento: casos críticos de compliance + dados
        # históricos de atendimento (src/pipeline.ipynb).
        self.escalation_policy = EscalationPolicy()

        # Conexão OpenSearch Serverless
        if self.endpoint:
            credentials = boto3.Session().get_credentials()
            awsauth = AWS4Auth(
                credentials.access_key,
                credentials.secret_key,
                "us-east-1",
                "aoss",
                session_token=credentials.token,
            )
            self.opensearch = OpenSearch(
                hosts=[{"host": self.endpoint.replace("https://", "").rstrip("/"), "port": 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True,
            )
        else:
            self.opensearch = None

    def _apply_output_guardrails(self, response_text: str, sources: list) -> tuple[bool, str]:
        """
        Garante que a resposta cite as fontes exatamente uma vez. O prompt
        instrui o modelo a NÃO citar fontes no corpo do texto — a citação é
        sempre adicionada aqui, de forma determinística, para não duplicar.
        """
        if not sources:
            return True, response_text
        has_citation = all(src.lower() in response_text.lower() for src in sources)
        if not has_citation:
            response_text += f"\n\n[Fonte consultada: {', '.join(sources)}]"
        return True, response_text

    def _generate_query_embedding(self, query_text: str) -> list[float]:
        """Gera o embedding da pergunta usando o Cohere Embed v4 no Bedrock."""
        body = json.dumps({
            "texts": [query_text],
            "input_type": "search_query",
        })

        response = self.bedrock.invoke_model(
            modelId="cohere.embed-v4:0",
            body=body,
        )
        response_body = json.loads(response["body"].read())

        embeddings_data = response_body.get("embeddings")
        if isinstance(embeddings_data, dict) and "float" in embeddings_data:
            return embeddings_data["float"][0]
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
                            "k": top_k,
                        }
                    }
                },
            }

            response = self.opensearch.search(index=target_index, body=search_query)
            results = []
            for hit in response["hits"]["hits"]:
                source_data = hit["_source"]

                doc_status = source_data.get("status", "vigente")
                if doc_status != "vigente":
                    continue

                results.append({
                    "score": hit["_score"],
                    "doc_id": source_data.get("doc_family_id") or source_data.get("id"),
                    "content": source_data.get("content") or source_data.get("text_content", ""),
                    "source_ref": source_data.get("source") or source_data.get("source_file", "documento_oficial"),
                })
            return results
        except Exception as e:
            print(f"Erro ao consultar OpenSearch ({self._resolve_index_name()}): {e}")
            return []

    def _extract_produto_servico(self, query: str, chunks: list) -> str:
        """Heurística simples para preencher 'produto_servico_envolvido' no handoff."""
        query_lower = query.lower()
        for produto in _PRODUTOS_CONHECIDOS:
            if produto in query_lower:
                return produto.title()
        for c in chunks:
            doc_id = (c.get("doc_id") or "").lower()
            if doc_id.startswith("plano-"):
                return doc_id.replace("plano-", "Plano ").replace("-", " ").title()
        return "Não identificado — confirmar com o cliente."

    def _build_handoff(
        self,
        user_id: str,
        query: str,
        conversation_history: list,
        escalation,
        chunks: list,
        canal_origem: str,
        dados_contato_retorno: Optional[str],
    ) -> dict:
        """
        Monta o registro de escalonamento com os 10 campos mínimos exigidos
        pela Política de Suporte e Escalonamento do corpus.

        Critério de qualidade: não basta ter os 10 campos presentes — um
        avaliador que recebe SÓ este dicionário (sem ver a conversa) precisa
        conseguir responder, olhando só para ele: (1) qual é o problema
        relatado, (2) o que já foi verificado pelo assistente e (3) qual é a
        urgência. Por isso 'historico_ja_levantado' é uma lista de fatos já
        resumidos, não um dump da conversation_history.
        """
        fontes_consultadas = list(dict.fromkeys([c["source_ref"] for c in chunks]))
        if not fontes_consultadas:
            fontes_consultadas = ["Nenhum documento localizado na base vigente para esta pergunta"]

        mensagens_anteriores_cliente = [
            m.get("content", "")
            for m in conversation_history
            if isinstance(m, dict) and m.get("role") == "user" and m.get("content")
        ]

        historico_ja_levantado = []
        if mensagens_anteriores_cliente:
            historico_ja_levantado.append(
                "Mensagens anteriores do cliente nesta conversa: " + " | ".join(mensagens_anteriores_cliente)
            )
        historico_ja_levantado.append(f"Motivo do escalonamento identificado pelo assistente: {escalation.rotulo_original}")
        if escalation.origem == "dados_historicos":
            historico_ja_levantado.append(
                f"Baseado em dado histórico de atendimento: {escalation.taxa_encaminhamento_pct}% dos "
                f"chamados de '{escalation.rotulo_original}' ({escalation.chamados} chamados analisados) "
                f"foram encaminhados a humano; satisfação média histórica: {escalation.satisfacao_media}."
            )
        historico_ja_levantado.append(
            "Documento(s) da base vigente consultado(s) pelo assistente antes de escalonar: "
            + ", ".join(fontes_consultadas)
        )

        return {
            # --- Os 10 campos mínimos exigidos pela politica_suporte_escalonamento.md ---
            "protocolo_atendimento": f"ESC-{uuid.uuid4().hex[:10].upper()}",
            "data_hora_abertura": datetime.now(timezone.utc).isoformat(),
            "canal_origem": canal_origem,
            "categoria_motivo": escalation.categoria_motivo,
            "resumo_caso": f'Cliente relatou: "{query}". Classificado como "{escalation.rotulo_original}".',
            "historico_ja_levantado": historico_ja_levantado,
            "produto_servico_envolvido": self._extract_produto_servico(query, chunks),
            "documento_fonte_consultado": fontes_consultadas,
            "urgencia": "Alta" if escalation.origem == "caso_critico" or (escalation.taxa_encaminhamento_pct or 0) >= 75 else "Média",
            "dados_contato_retorno": dados_contato_retorno or "Não informado nesta interação — atendente deve solicitar ao cliente no retorno.",
            # --- Campos extras, além do mínimo exigido, mantidos para auditoria ---
            "user_id": user_id,
            "evidencia_dados": {
                "origem_da_decisao": escalation.origem,
                "subcategoria": escalation.subcategoria,
                "taxa_encaminhamento_historica_pct": escalation.taxa_encaminhamento_pct,
                "chamados_analisados": escalation.chamados,
                "fonte": "src/pipeline.ipynb -> data/processed/log_chamados/politica_escalonamento.json",
            },
        }

    def process_message(
        self,
        user_id: str,
        query: str,
        conversation_history: list = None,
        canal_origem: str = "chat",
        dados_contato_retorno: Optional[str] = None,
    ):
        conversation_history = conversation_history or []
        guardrail_blocked_msg = "Como assistente da ConectaTel, só posso responder a dúvidas sobre nossos planos, faturas e serviços oficiais."

        # 1. Guardrail de Entrada (Intercepta Prompt Injection e Fora de Escopo ANTES do RAG)
        if self.guardrail_id:
            try:
                guard_res = self.bedrock.apply_guardrail(
                    guardrailIdentifier=self.guardrail_id,
                    guardrailVersion=self.guardrail_version,
                    source="INPUT",
                    content=[{"text": {"text": query}}],
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
                    return {"response": guardrail_blocked_msg, "status": "BLOCKED_GUARDRAIL", "trace_id": trace_id}
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
                    return {"response": guardrail_blocked_msg, "status": "BLOCKED_GUARDRAIL", "trace_id": trace_id}

        # 2. Busca RAG no OpenSearch — roda SEMPRE (mesmo antes de decidir
        #    escalonamento), para que o registro de handoff sempre tenha
        #    "documento_fonte_consultado" preenchido, mesmo quando insuficiente
        #    para responder — conforme exige a Política de Suporte e Escalonamento.
        chunks = self.retrieve_vigente_chunks(query)
        max_score = max([c["score"] for c in chunks]) if chunks else 0.0

        # 3. Triagem de Escalonamento Humano — casos críticos de compliance
        #    (Critérios 1-6 da política) + dados históricos de atendimento
        #    (src/pipeline.ipynb -> politica_escalonamento.json).
        escalation = self.escalation_policy.match(query)
        if escalation and escalation.escalar:
            handoff = self._build_handoff(
                user_id=user_id,
                query=query,
                conversation_history=conversation_history,
                escalation=escalation,
                chunks=chunks,
                canal_origem=canal_origem,
                dados_contato_retorno=dados_contato_retorno,
            )
            resp = "Entendo a situação. Estou encaminhando seu caso para um atendente humano especializado."
            trace_id = log_trace(
                pergunta=query,
                user_id=user_id,
                decisao="ESCALATED",
                guardrail_acionado=f"escalonamento:{escalation.origem}:{escalation.subcategoria}",
                chunk_strategy=self.chunk_strategy,
                resposta=resp,
                extra={"handoff": handoff},
            )
            return {"response": resp, "status": "ESCALATED", "handoff": handoff, "trace_id": trace_id}

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
            "Não inclua nomes de arquivo nem uma seção de 'Fontes' na sua resposta — "
            "a citação das fontes é adicionada automaticamente depois, fora do seu texto.\n\n"
            f"Contexto:\n{context}\n\nPergunta: {query}"
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        })

        invoke_kwargs = {
            "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "body": body,
        }

        if self.guardrail_id:
            invoke_kwargs["guardrailIdentifier"] = self.guardrail_id
            invoke_kwargs["guardrailVersion"] = self.guardrail_version

        try:
            res = self.bedrock.invoke_model(**invoke_kwargs)

            headers = res.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            guardrail_action = (
                res.get("amazon-bedrock-guardrailAction")
                or headers.get("amazon-bedrock-guardrailaction", "")
            ).upper()

            raw_answer = json.loads(res["body"].read())["content"][0]["text"]

            if guardrail_action == "INTERVENED" or guardrail_blocked_msg.lower() in raw_answer.lower():
                trace_id = log_trace(
                    pergunta=query,
                    user_id=user_id,
                    decisao="BLOCKED_GUARDRAIL",
                    score_max=max_score,
                    fontes=[c["source_ref"] for c in chunks],
                    guardrail_acionado="bedrock_guardrail_output",
                    chunk_strategy=self.chunk_strategy,
                    resposta=guardrail_blocked_msg,
                )
                return {"response": guardrail_blocked_msg, "status": "BLOCKED_GUARDRAIL", "trace_id": trace_id}

        except Exception as e:
            if "Guardrail" in str(e) or "intervened" in str(e).lower():
                trace_id = log_trace(
                    pergunta=query,
                    user_id=user_id,
                    decisao="BLOCKED_GUARDRAIL",
                    score_max=max_score,
                    fontes=[c["source_ref"] for c in chunks],
                    guardrail_acionado="bedrock_guardrail_output",
                    chunk_strategy=self.chunk_strategy,
                    resposta=guardrail_blocked_msg,
                )
                return {"response": guardrail_blocked_msg, "status": "BLOCKED_GUARDRAIL", "trace_id": trace_id}
            raise e

        # 6. Pós-processamento de Saída e Fontes
        sources = list(dict.fromkeys([c["source_ref"] for c in chunks]))
        _, final_answer = self._apply_output_guardrails(raw_answer, sources)

        trace_id = log_trace(
            pergunta=query,
            user_id=user_id,
            decisao="ANSWERED",
            score_max=max_score,
            fontes=sources,
            chunk_strategy=self.chunk_strategy,
            resposta=final_answer,
        )

        return {"response": final_answer, "status": "ANSWERED", "sources": sources, "trace_id": trace_id}