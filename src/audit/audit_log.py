"""
Trilha de auditoria do Concierge ConectaTel (Parte 5).

Cada resposta do agente gera um registro (trace) contendo pergunta, fonte
consultada, score de recuperação, decisão tomada e o guardrail acionado
(se algum). O registro é gravado em JSONL, um arquivo por linha, o que
permite:
  - escrita simples e append-only (sem precisar de banco externo);
  - consulta local e imediata por trace_id, sem depender de ferramentas
    de log gerenciadas (CloudWatch etc.), como exigido no enunciado.

Uso típico dentro do agente (ver src/agent/agent.py):

    from src.audit.audit_log import log_trace

    trace_id = log_trace(
        user_id=user_id,
        pergunta=query,
        fontes=sources,          # lista de source_ref, pode ser []
        score_max=max_score,     # float, pode ser None
        decisao="ANSWERED",      # ANSWERED | ESCALATED | NO_KNOWLEDGE | BLOCKED_GUARDRAIL
        guardrail_acionado=None, # nome do guardrail, ou None
        chunk_strategy=self.chunk_strategy,  # qual índice/estratégia gerou a resposta
        resposta=final_answer,
        extra={"handoff": handoff}  # opcional, qualquer contexto adicional
    )

Consulta durante a banca (via CLI, ver src/audit/lookup_cli.py):

    python -m src.audit.lookup_cli cct-8f2a1b3c
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Caminho do log. Pode ser sobrescrito com a env var AUDIT_LOG_PATH,
# útil se quiserem apontar para um disco montado no Lambda (ex.: /tmp)
# ou para um caminho dentro do bucket S3 sincronizado localmente.
DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "audit" / "audit_log.jsonl"


def _log_path() -> Path:
    path = Path(os.getenv("AUDIT_LOG_PATH", str(DEFAULT_LOG_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def generate_trace_id() -> str:
    """Gera um trace_id curto e legível: cct-XXXXXXXX."""
    return f"cct-{uuid.uuid4().hex[:8]}"


def log_trace(
    *,
    pergunta: str,
    decisao: str,
    user_id: Optional[str] = None,
    fontes: Optional[list[str]] = None,
    score_max: Optional[float] = None,
    guardrail_acionado: Optional[str] = None,
    resposta: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
    trace_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Registra uma interação na trilha de auditoria e retorna o trace_id.

    decisao esperado: "ANSWERED" | "ESCALATED" | "NO_KNOWLEDGE" | "BLOCKED_GUARDRAIL"
    (mesmos valores de status já usados em ConciergeAgent.process_message).

    chunk_strategy: qual estratégia de chunking/índice gerou os resultados
    dessa interação ("fixed_windows" | "full_document" |
    "hierarchical_semantic"). Registrar isso permite reconstruir, na
    pergunta de auditoria, não só a fonte citada mas também qual índice
    estava ativo quando a resposta foi gerada.
    """
    trace_id = trace_id or generate_trace_id()

    record = {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "pergunta": pergunta,
        "fontes": fontes or [],
        "score_max": score_max,
        "decisao": decisao,
        "guardrail_acionado": guardrail_acionado,
        "chunk_strategy": chunk_strategy,
        "resposta": resposta,
    }
    if extra:
        record["extra"] = extra

    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return trace_id


def get_trace(trace_id: str) -> Optional[dict[str, Any]]:
    """Busca um registro pelo trace_id. Retorna None se não encontrado.

    Leitura linear simples: para o volume de um hackathon (algumas
    centenas de interações no máximo) isso responde em milissegundos,
    o que é o que importa para o requisito de reconstrução em até 60s.
    """
    path = _log_path()
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("trace_id") == trace_id:
                return record
    return None


def format_trace(record: dict[str, Any]) -> str:
    """Formata um registro para apresentação legível (usado na banca)."""
    if not record:
        return "Trace não encontrado."

    lines = [
        f"trace_id          : {record.get('trace_id')}",
        f"timestamp         : {record.get('timestamp')}",
        f"usuário           : {record.get('user_id')}",
        f"pergunta          : {record.get('pergunta')}",
        f"fonte(s) consultada(s): {', '.join(record.get('fontes') or []) or '(nenhuma)'}",
        f"score máximo      : {record.get('score_max')}",
        f"decisão           : {record.get('decisao')}",
        f"guardrail acionado: {record.get('guardrail_acionado') or '(nenhum)'}",
        f"estratégia de chunking: {record.get('chunk_strategy') or '(não registrada)'}",
        f"resposta dada     : {record.get('resposta')}",
    ]
    if record.get("extra"):
        lines.append(f"contexto adicional: {json.dumps(record['extra'], ensure_ascii=False)}")
    return "\n".join(lines)