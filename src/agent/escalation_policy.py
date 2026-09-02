"""
Política de escalonamento humano baseada em dados empíricos de atendimento
+ critérios de compliance explícitos da politica_suporte_escalonamento.md.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "processed" / "log_chamados" / "politica_escalonamento.json"
)

# Limiar do Critério 2 da política (contestação de fatura >= R$500 exige
# verificação antifraude obrigatória, independente de estatística histórica).
LIMIAR_CONTESTACAO_VALOR_ALTO = 500.0

_MONEY_PATTERN = re.compile(r"r\$\s*([\d\.]+,\d{2}|[\d\.]+|\d+)")


def _extract_max_money_value(text: str) -> Optional[float]:
    """Extrai o maior valor monetário (R$ ...) mencionado no texto, se houver."""
    values: List[float] = []
    for raw in _MONEY_PATTERN.findall(text.lower()):
        normalized = raw.replace(".", "").replace(",", ".")
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return max(values) if values else None


# Mapeamento curado de expressões do texto do cliente para as subcategorias
# reais do log de chamados (data/raw/log_chamados/log_chamados_sintetico.csv).
# Cobre TODAS as subcategorias com escalar_por_padrao=true no JSON gerado
# pelo notebook, além de outras relevantes com dado histórico disponível.
_KEYWORD_TO_SUBCATEGORIA = {
    # --- Alta taxa histórica de encaminhamento (escalar_por_padrao=true) ---
    "sem sinal": [
        "sem sinal", "não tenho sinal", "nao tenho sinal",
        "zero de sinal", "aparelho sem sinal", "não pega sinal", "nao pega sinal",
    ],
    "sinal instável": [
        "sinal instável", "sinal instavel", "sinal cai direto", "sinal cai toda hora",
        "internet cai toda hora", "quedas de sinal", "sinal indo e voltando",
    ],
    "falha na instalação": [
        "falha na instalação", "falha na instalacao", "técnico não veio", "tecnico nao veio",
        "instalação não foi concluída", "instalacao nao foi concluida",
        "problema na instalação da internet fixa", "internet fixa não foi instalada",
    ],
    # --- Demais subcategorias com dado histórico disponível ---
    "cobrança indevida": ["cobrança indevida", "cobranca indevida", "cobraram errado", "cobrado errado"],
    "contestação de valor": ["contestar", "contestação", "contestacao", "valor errado na fatura"],
    "valor divergente": ["valor divergente", "valor diferente do combinado"],
    "cancelamento por insatisfação": ["cancelar por insatisfação", "quero cancelar", "não quero mais o plano", "nao quero mais o plano"],
    "cancelamento de linha": ["encerrar a linha", "encerrar o plano", "cancelar a linha", "cancelamento de linha"],
    "cancelamento de complemento": ["cancelar o complemento", "cancelar pacote adicional"],
    "cancelamento por mudança de operadora": ["mudar de operadora", "trocar de operadora e cancelar"],
    "aparelho não conecta": ["aparelho não conecta", "aparelho nao conecta", "celular não conecta"],
    "chip não funciona": ["chip não funciona", "chip nao funciona", "chip parou de funcionar"],
    "portabilidade não concluída": ["portabilidade não concluída", "portabilidade nao concluida", "portabilidade travada"],
}

# Critérios explícitos de escalonamento obrigatório da politica_suporte_escalonamento.md.
# Sempre escalam, independente de estatística — porque são raros/inexistentes no log
# sintético (uma taxa histórica de 0% aqui NÃO significa que o risco seja baixo).
_CASOS_CRITICOS_SEMPRE_ESCALA = {
    "suspeita de fraude": (
        ["fraude", "golpe", "uso indevido da minha linha", "sim swap", "clonaram meu chip"],
        "Critério 1 da Política de Suporte — suspeita de fraude",
    ),
    "contestação de multa de fidelidade": (
        ["multa de fidelidade", "contestar a multa", "discordo da multa", "multa cobrada errada", "multa injusta"],
        "Critério 3 da Política de Suporte — contestação de multa de fidelidade",
    ),
    "titularidade / óbito": (
        ["falecimento", "óbito", "faleceu", "mudança de titularidade", "troca de titular"],
        "Critério 4 da Política de Suporte — alteração de titularidade / falecimento do titular",
    ),
    "reclamação em órgão externo": (
        ["anatel", "procon", "ação judicial", "processo contra a conectatel"],
        "Critério 5 da Política de Suporte — reclamação em órgão externo / ação judicial",
    ),
    "assédio ou conduta abusiva": (
        ["assédio", "discriminação", "atendente me tratou mal", "conduta abusiva"],
        "Critério 6 da Política de Suporte — relato de assédio, discriminação ou conduta abusiva",
    ),
}


@dataclass
class EscalationDecision:
    subcategoria: str
    rotulo_original: str
    escalar: bool
    origem: str  # "dados_historicos" ou "caso_critico"
    categoria_motivo: str
    taxa_encaminhamento_pct: Optional[float] = None
    satisfacao_media: Optional[float] = None
    chamados: int = 0


class EscalationPolicy:
    def __init__(self, policy_path: Optional[Path] = None):
        self.policy_path = Path(policy_path) if policy_path else Path(
            os.getenv("ESCALATION_POLICY_PATH", str(DEFAULT_POLICY_PATH))
        )
        self._data = self._load()

    def _load(self) -> dict:
        if not self.policy_path.exists():
            # Fail-open: sem o JSON do notebook o agente continua funcionando,
            # só os casos críticos (independentes de dado histórico) ficam ativos.
            return {"subcategorias": {}, "limiar_taxa_encaminhamento_pct": None}
        with open(self.policy_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def match(self, query: str) -> Optional[EscalationDecision]:
        query_lower = query.lower()

        # 1. Critério 2 — contestação de fatura de valor alto (regra numérica
        #    de compliance, não depende de estatística nem de keyword fixa).
        if any(kw in query_lower for kw in ["contestar", "contestação", "contestacao"]):
            valor = _extract_max_money_value(query)
            if valor is not None and valor >= LIMIAR_CONTESTACAO_VALOR_ALTO:
                return EscalationDecision(
                    subcategoria="contestação de valor alto",
                    rotulo_original=f"Contestação de fatura de R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", "."),
                    escalar=True,
                    origem="caso_critico",
                    categoria_motivo=(
                        f"Critério 2 da Política de Suporte — contestação de fatura de "
                        f"R$ {valor:,.2f} (>= R$ {LIMIAR_CONTESTACAO_VALOR_ALTO:,.2f}), "
                        "verificação antifraude obrigatória"
                    ),
                )

        # 2. Demais critérios críticos (1, 3, 4, 5, 6) — sempre escalam.
        for rotulo, (keywords, categoria_motivo) in _CASOS_CRITICOS_SEMPRE_ESCALA.items():
            if any(kw in query_lower for kw in keywords):
                return EscalationDecision(
                    subcategoria=rotulo,
                    rotulo_original=rotulo,
                    escalar=True,
                    origem="caso_critico",
                    categoria_motivo=categoria_motivo,
                )

        # 3. Dados históricos de atendimento (pipeline.ipynb).
        subcategorias = self._data.get("subcategorias", {})
        for subcategoria_key, keywords in _KEYWORD_TO_SUBCATEGORIA.items():
            if any(kw in query_lower for kw in keywords):
                stats = subcategorias.get(subcategoria_key)
                if not stats:
                    continue
                escalar = bool(stats.get("escalar_por_padrao", False))
                taxa = stats.get("taxa_encaminhamento_pct")
                categoria_motivo = (
                    f"Critério interno adicional (dado histórico de atendimento) — "
                    f"subcategoria '{stats.get('rotulo_original', subcategoria_key)}' com "
                    f"{taxa}% de encaminhamento a humano em {stats.get('chamados', 0)} "
                    f"chamados analisados (limiar: {self._data.get('limiar_taxa_encaminhamento_pct')}%)"
                )
                return EscalationDecision(
                    subcategoria=subcategoria_key,
                    rotulo_original=stats.get("rotulo_original", subcategoria_key),
                    escalar=escalar,
                    origem="dados_historicos",
                    categoria_motivo=categoria_motivo,
                    taxa_encaminhamento_pct=taxa,
                    satisfacao_media=stats.get("satisfacao_media"),
                    chamados=stats.get("chamados", 0),
                )
        return None