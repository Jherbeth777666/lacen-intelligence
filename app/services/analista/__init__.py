"""Analista IA do LACEN — ponto de entrada único.

Fluxo:
  pergunta → (Claude com ferramentas | modo offline) → resposta
           + "Base analisada" calculada pelo SISTEMA (não pelo modelo)
           + registro em ia_consultas (governança)
"""
from __future__ import annotations

import json
from datetime import date
import logging

from sqlalchemy import text

from app.config import Config
from app.db import transacao
from app.services import indicadores as ind

from . import offline

log = logging.getLogger(__name__)

SUGESTOES = [   # (tema, cor do módulo, perguntas) — tela inicial do Analista
    ("Agenda e faltas", "#199e70", ["Qual UBS merece maior atenção?", "Em que dia da semana cada UBS mais falta?",
                                     "Quantos pacientes faltam repetidamente?"]),
    ("Laboratório", "#c98500", ["Quais exames mais cresceram?", "Como evoluiu o hemograma mês a mês?",
                                "Como está a jornada do paciente, do agendamento ao laboratório?"]),
    ("Gestão", "#d55181", ["O que o gestor deveria priorizar?", "Compare o mês atual com o anterior",
                           "Quais UBS tiveram mais presenças em todo o período?"]),
]

PERGUNTAS_EXEMPLO = [
    "Qual UBS apresentou maior número de faltas?",
    "Quais UBS têm o melhor comparecimento?",
    "Como está a jornada do paciente, do agendamento ao laboratório?",
    "Quantos pacientes faltam repetidamente?",
    "Qual foi a evolução das faltas mês a mês?",
    "Quais exames tiveram maior crescimento?",
    "Qual UBS merece maior atenção?",
    "O que o gestor deveria priorizar?",
]

PEDIDO_RELATORIO = ("Gere um relatório executivo de {periodo} para a gestão do LACEN, com as seções: Resumo, "
                    "Principais números (agendamentos, comparecimento, faltas, atendimentos e exames, comparando com o "
                    "período anterior), Pontos de atenção, Destaques positivos (UBS com melhor comparecimento ou que "
                    "mais melhoraram) e Próximos passos.")


def modo_ativo() -> str:
    return "OFFLINE" if Config.ia()["provedor"] == "offline" else "LLM"


def _grafico(usadas: list[dict], per) -> dict | None:
    """Gráfico automático pelo que foi consultado (quando o modelo não escolhe, e no modo offline)."""
    from .graficos import automatico
    return automatico(usadas, per)


def perguntar(pergunta: str, usuario_id: int | None = None, historico=None) -> dict:
    pergunta = (pergunta or "").strip()[:1200]   # 1000 da caixa + o período escolhido no seletor
    if not pergunta:
        return {"erro": "Escreva uma pergunta."}

    aviso, modelo_usado = None, None
    if modo_ativo() == "LLM":
        try:
            from .llm import responder as responder_llm
            ia = Config.ia()
            r = responder_llm(pergunta, ia["provedor"], ia["chave"], ia["modelo"], historico)
            modo = "LLM"
            modelo_usado = r.get("modelo") or ia["modelo"]
            periodos = r.get("periodos") or []
            per = (ind.Periodo(*[date.fromisoformat(x) for x in periodos[0]])
                   if periodos else ind.periodo_padrao())
            usadas = r["ferramentas"]
            ferramentas = [f["ferramenta"] for f in usadas]
        except Exception as e:  # sem rede, chave inválida, limite… → nunca deixa o gestor sem resposta
            from .llm import motivo_legivel
            log.warning("Falha no modo LLM, usando offline: %s", e)
            aviso = (f"A IA generativa não respondeu: {motivo_legivel(e)}. "
                     "Esta análise foi gerada pelo modo analítico offline, com os mesmos números do banco.")
            r = offline.responder(pergunta)
            modo, per, ferramentas = "OFFLINE", r["periodo"], r["ferramentas"]
            usadas = [{"ferramenta": f} for f in ferramentas]
    else:
        r = offline.responder(pergunta)
        modo, per, ferramentas = "OFFLINE", r["periodo"], r["ferramentas"]
        usadas = [{"ferramenta": f} for f in ferramentas]

    base = ind.base_analisada(per)
    resposta = r["resposta"]
    with transacao(limpa_cache=False) as c:
        c.execute(text("""INSERT INTO ia_consultas (usuario_id, pergunta, modo, ferramentas, resposta, base)
                          VALUES (:u, :p, :m, CAST(:f AS jsonb), :r, CAST(:b AS jsonb))"""),
                  {"u": usuario_id, "p": pergunta, "m": modo, "f": json.dumps(ferramentas),
                   "r": resposta, "b": json.dumps(base, default=str)})
    graficos = list(r.get("graficos") or [])          # os que a IA escolheu (modo LLM)
    if not graficos:
        try:
            g = _grafico(usadas, per)
            graficos = [g] if g else []
        except Exception as e:   # gráfico é complemento: nunca derruba a resposta
            log.warning("Gráfico da resposta não gerado: %s", e)
    ferramentas = [f for f in ferramentas if f != "mostrar_grafico"]
    return {"resposta": resposta, "modo": modo, "aviso": aviso, "base": base,
            "ferramentas": sorted(set(ferramentas)), "graficos": graficos,
            "modelo": modelo_usado, "sugestoes": list(r.get("sugestoes") or [])}
