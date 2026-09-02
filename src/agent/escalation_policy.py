"""
Política de escalonamento humano baseada em dados empíricos de atendimento.

Fluxo:
  1. pipeline.ipynb calcula, por subcategoria de chamado, taxa_encaminhamento,
     satisfacao_media etc. e exporta o JSON.
  2. Este módulo carrega o JSON e mantém um dicionário pequeno e auditável de
     palavras-chave -> subcategoria, para mapear o texto livre da pergunta do
     cliente a uma subcategoria conhecida do log de chamados.
  3. Se a subcategoria encontrada tiver "escalar_por_padrao" = true (ou seja,
     historicamente >= limiar% dos chamados daquela subcategoria foram
     parar com um humano), o agente escalona — com a estatística registrada
     no trace, em vez de ser uma decisão de caixa-preta.

Reexecutar o notebook com dados mais recentes atualiza a política sem tocar
em código do agente.

IMPORTANTE — casos críticos e raros: a lista abaixo (_CASOS_CRITICOS_SEMPRE_ESCALA)
cobre situações descritas como escalonamento obrigatório em
politica_suporte_escalonamento.md que podem ter poucas ou nenhuma ocorrência
no log histórico (ex.: suspeita de fraude, óbito do titular). Para esses
casos, uma taxa estatística não é confiável — por isso eles sempre escalam,
independente do que o JSON disser.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "processed" / "log_chamados" / "politica_escalonamento.json"
)

# Mapeamento curado de expressões do texto do cliente para as subcategorias
# reais do log de chamados (data/raw/log_chamados/log_chamados_sintetico.csv).
# Mantido pequeno e explícito de propósito: fácil de auditar e corrigir, sem
# depender de um classificador adicional.
_KEYWORD_TO_SUBCATEGORIA = {
    "cobrança indevida": ["cobrança indevida", "cobranca indevida", "cobraram errado", "cobrado errado"],
    "contestação de valor": ["contestar", "contestação", "contestacao", "valor errado na fatura", "fatura indevida"],
    "valor divergente": ["valor divergente", "valor diferente do combinado"],
    "cancelamento por insatisfação": ["cancelar por insatisfação", "quero cancelar", "não quero mais o plano", "nao quero mais o plano"],
    "cancelamento de linha": ["encerrar a linha", "encerrar o plano", "cancelar a linha", "cancelamento de linha"],
    "cancelamento de complemento": ["cancelar o complemento", "cancelar pacote adicional"],
    "cancelamento por mudança de operadora": ["mudar de operadora", "trocar de operadora e cancelar"],
    "aparelho não conecta": ["aparelho não conecta", "aparelho nao conecta", "celular não conecta"],
    "chip não funciona": ["chip não funciona", "chip nao funciona", "chip parou de funcionar"],
    "portabilidade não concluída": ["portabilidade não concluída", "portabilidade nao concluida", "portabilidade travada"],
}

# Casos de baixa frequência histórica, mas escalonamento sempre obrigatório
# conforme politica_suporte_escalonamento.md — não dependem da taxa do JSON.
_CASOS_CRITICOS_SEMPRE_ESCALA = {
    "suspeita de fraude": ["fraude", "golpe", "uso indevido da minha linha", "sim swap", "clonaram meu chip"],
    "titularidade / óbito": ["falecimento", "óbito", "faleceu", "mudança de titularidade"],
    "reclamação em órgão externo": ["anatel", "procon", "ação judicial", "processo contra a conectatel"],
    "assédio ou conduta abusiva": ["assédio", "discriminação", "atendente me tratou mal", "conduta abusiva"],
}


@dataclass
class EscalationDecision:
    subcategoria: str
    rotulo_original: str
    escalar: bool
    origem: str  # "dados_historicos" ou "caso_critico"
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
            # Sem o JSON exportado do pipeline.ipynb, o agente continua
            # funcionando (fail-open, para não travar o atendimento), mas
            # nenhuma subcategoria terá dado empírico — só os casos críticos
            # continuam ativos.
            return {"subcategorias": {}, "limiar_taxa_encaminhamento_pct": None}
        with open(self.policy_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def match(self, query: str) -> Optional[EscalationDecision]:
        """
        Verifica primeiro os casos críticos (sempre escalam, independente de
        estatística) e depois as subcategorias com dado histórico suficiente.
        Retorna None se nenhuma regra bateu com o texto da pergunta.
        """
        query_lower = query.lower()

        for rotulo, keywords in _CASOS_CRITICOS_SEMPRE_ESCALA.items():
            if any(kw in query_lower for kw in keywords):
                return EscalationDecision(
                    subcategoria=rotulo,
                    rotulo_original=rotulo,
                    escalar=True,
                    origem="caso_critico",
                )

        subcategorias = self._data.get("subcategorias", {})
        for subcategoria_key, keywords in _KEYWORD_TO_SUBCATEGORIA.items():
            if any(kw in query_lower for kw in keywords):
                stats = subcategorias.get(subcategoria_key)
                if not stats:
                    continue
                return EscalationDecision(
                    subcategoria=subcategoria_key,
                    rotulo_original=stats.get("rotulo_original", subcategoria_key),
                    escalar=bool(stats.get("escalar_por_padrao", False)),
                    origem="dados_historicos",
                    taxa_encaminhamento_pct=stats.get("taxa_encaminhamento_pct"),
                    satisfacao_media=stats.get("satisfacao_media"),
                    chamados=stats.get("chamados", 0),
                )
        return None