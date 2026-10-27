"""Gráficos das respostas do Analista — sempre montados pelo SISTEMA com dados do banco.

O modelo só escolhe QUAL gráfico mostrar (ferramenta mostrar_grafico); os números nunca vêm dele.
Formato: {"tipo": "barra_h"|"barra"|"linha", "titulo", "rotulos", "series": [{"nome", "valores"}], "pct"}
"""
from __future__ import annotations

from app.services import indicadores as ind
from app.services.alertas import nome_ubs

TIPOS = {
    "ranking_faltas_ubs": "Taxa de faltas por UBS no período",
    "ranking_comparecimento_ubs": "Taxa de comparecimento por UBS no período",
    "ranking_presencas_ubs": "Número de presenças (pessoas que compareceram) por UBS no período",
    "evolucao_faltas_mensal": "Taxa de faltas mês a mês (rede ou UBS)",
    "evolucao_atendimentos_mensal": "Atendimentos e exames mês a mês (rede ou UBS)",
    "serie_diaria": "Agendados, faltas e atendimentos dia a dia no período (rede ou UBS)",
    "faltas_dia_semana": "Taxa de faltas por dia da semana (rede ou UBS)",
    "exames_mais_realizados": "Exames mais realizados no período",
    "exame_evolucao": "Quantidade mensal de um exame específico (parâmetro exame)",
    "jornada_paciente": "Funil agendados → compareceram → atendidos no laboratório",
    "faltas_repetidas": "Pacientes por número de faltas no período",
}


def _s(nome, valores):
    return {"nome": nome, "valores": [None if v is None else float(v) for v in valores]}


def montar(tipo: str, per, ubs: dict | None = None, exame: str | None = None) -> dict | None:
    uid = ubs["id"] if ubs else None
    alvo = nome_ubs(ubs["nome"]) if ubs else "rede"
    rot = per.rotulo()
    if tipo in ("ranking_faltas_ubs", "ranking_comparecimento_ubs"):
        campo = "taxa_falta" if tipo == "ranking_faltas_ubs" else "taxa_comparecimento"
        rk = [r for r in ind.ranking_ubs(per) if (r["realizados"] or 0) >= 30 and r[campo] is not None]
        rk = sorted(rk, key=lambda r: -r[campo])[:15]
        if rk:
            nome = "Taxa de faltas" if campo == "taxa_falta" else "Comparecimento"
            return {"tipo": "barra_h", "titulo": f"{nome} por UBS · {rot}", "pct": True,
                    "rotulos": [nome_ubs(r["ubs"]) for r in rk], "series": [_s(nome, [r[campo] for r in rk])]}
    if tipo == "ranking_presencas_ubs":
        rk = sorted([r for r in ind.ranking_ubs(per) if r["compareceram"]], key=lambda r: -r["compareceram"])[:15]
        if rk:
            return {"tipo": "barra_h", "titulo": f"Presenças por UBS · {rot}", "pct": False,
                    "rotulos": [nome_ubs(r["ubs"]) for r in rk], "series": [_s("Presenças", [r["compareceram"] for r in rk])]}
    if tipo == "evolucao_faltas_mensal":
        m = [x for x in ind.evolucao_mensal(uid) if x["taxa_falta"] is not None]
        if m:
            return {"tipo": "linha", "titulo": f"Taxa de faltas por mês · {alvo}", "pct": True, "rotulos": [x["mes"] for x in m],
                    "series": [_s("Taxa de faltas", [x["taxa_falta"] for x in m])]}
    if tipo == "evolucao_atendimentos_mensal":
        m = ind.evolucao_mensal(uid)
        if m:
            return {"tipo": "barra", "titulo": f"Atendimentos por mês · {alvo}", "pct": False, "rotulos": [x["mes"] for x in m],
                    "series": [_s("Atendimentos", [x["atendimentos"] for x in m])]}
    if tipo == "serie_diaria":
        d = [x for x in ind.serie_diaria(per, uid) if x["agendados"] or x["atendimentos"]]
        if d:
            return {"tipo": "linha", "titulo": f"Dia a dia · {alvo} · {rot}", "pct": False,
                    "rotulos": [x["data"].strftime("%d/%m") for x in d],
                    "series": [_s("Agendados", [x["agendados"] for x in d]), _s("Faltas", [x["faltas"] for x in d]),
                               _s("Atendimentos no laboratório", [x["atendimentos"] for x in d])]}
    if tipo == "faltas_dia_semana":
        d = [x for x in ind.faltas_dia_semana(per, uid) if x["taxa_falta"] is not None]
        if d:
            return {"tipo": "barra", "titulo": f"Taxa de faltas por dia da semana · {alvo} · {rot}", "pct": True,
                    "rotulos": [x["dia_semana"] for x in d], "series": [_s("Taxa de faltas", [x["taxa_falta"] for x in d])]}
    if tipo == "exames_mais_realizados":
        t = ind.top_exames(per, 12)
        if t:
            return {"tipo": "barra_h", "titulo": f"Exames mais realizados · {rot}", "pct": False,
                    "rotulos": [x["exame"] for x in t], "series": [_s("Exames", [x["quantidade"] for x in t])]}
    if tipo == "exame_evolucao" and exame:
        e = ind.exame_detalhe(exame, per)
        if e and e["mensal"]:
            return {"tipo": "barra", "titulo": f"{e['exame']} por mês", "pct": False, "rotulos": [x["mes"] for x in e["mensal"]],
                    "series": [_s("Exames", [x["quantidade"] for x in e["mensal"]])]}
    if tipo == "jornada_paciente":
        j = ind.jornada_paciente(per, uid)
        if j:
            return {"tipo": "barra_h", "titulo": f"Jornada do paciente · {alvo} · {rot}", "pct": False,
                    "rotulos": [x["etapa"] for x in j], "series": [_s("Pacientes", [x["quantidade"] for x in j])]}
    if tipo == "faltas_repetidas":
        r = ind.faltas_repetidas(per, uid)
        if r["faltas"]:
            return {"tipo": "barra", "titulo": f"Pacientes por número de faltas · {alvo} · {rot}", "pct": False,
                    "rotulos": [x["faixa"] for x in r["faixas"]], "series": [_s("Pacientes", [x["pacientes"] for x in r["faixas"]])]}
    return None


# quando o modelo não pede gráfico (ou no modo offline): escolhe pelo que foi consultado
AUTOMATICO = {"ranking_ubs": "ranking_faltas_ubs", "presencas_ubs": "ranking_presencas_ubs", "evolucao_mensal": "evolucao_faltas_mensal",
              "faltas_por_dia_semana": "faltas_dia_semana", "exames": "exames_mais_realizados",
              "jornada_do_paciente": "jornada_paciente", "faltas_repetidas": "faltas_repetidas",
              "serie_diaria": "serie_diaria", "detalhe_exame": "exame_evolucao"}


def automatico(usadas: list[dict], per) -> dict | None:
    from .ferramentas import localizar_ubs
    for u in usadas:
        tipo = AUTOMATICO.get(u.get("ferramenta"))
        if not tipo:
            continue
        args = u.get("parametros") or {}
        g = montar(tipo, per, localizar_ubs(args.get("ubs")) if args.get("ubs") else None, args.get("exame"))
        if g:
            return g
    return None
