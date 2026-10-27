"""Ferramentas que o Analista IA pode chamar.

A IA NÃO escreve SQL e NÃO vê dados pessoais: ela só pode pedir indicadores
deste catálogo fechado. Cada ferramenta devolve números calculados no banco +
o período usado, e esses números são a única fonte permitida para a resposta.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db import consultar
from app.services import alertas as al
from app.services import indicadores as ind
from app.services.indicadores import Periodo

from .graficos import TIPOS as GRAFICOS_TIPOS
from etl.normalizacao import normalizar_texto

_PERIODO = {
    "inicio": {"type": "string", "description": "Data inicial AAAA-MM-DD (opcional; padrão = último mês com dados)"},
    "fim": {"type": "string", "description": "Data final AAAA-MM-DD (opcional)"},
}
_UBS = {"ubs": {"type": "string", "description": "Nome (ou parte do nome) da UBS (opcional)"}}


def periodo_de(args: dict) -> Periodo:
    padrao = ind.periodo_padrao()
    try:
        ini = date.fromisoformat(args["inicio"]) if args.get("inicio") else padrao.inicio
        fim = date.fromisoformat(args["fim"]) if args.get("fim") else (padrao.fim if not args.get("inicio") else None)
    except ValueError:
        return padrao
    if fim is None:  # só início informado → até o fim do mês do início
        prox = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
        fim = prox - timedelta(days=1)
    return Periodo(min(ini, fim), max(ini, fim))


def localizar_ubs(nome: str | None) -> dict | None:
    if not nome:
        return None
    alvo = normalizar_texto(nome).replace("UBS ", "")
    for u in consultar("SELECT id, nome FROM ubs WHERE ativo ORDER BY length(nome)"):
        if alvo and alvo in normalizar_texto(u["nome"]):
            return u
    return None


def _com_periodo(fn):
    def wrap(args):
        per = periodo_de(args)
        return {"periodo": per.rotulo(), "periodo_anterior": per.anterior().rotulo(), "dados": fn(per, args)}
    return wrap


def _ubs_ou_erro(args):
    if not args.get("ubs"):
        return None, None
    u = localizar_ubs(args["ubs"])
    return (u, None) if u else (None, {"erro": f"UBS '{args['ubs']}' não encontrada na base."})


def _faltas_dia(per, args):
    u, erro = _ubs_ou_erro(args)
    return erro or {"ubs": u["nome"] if u else "Rede inteira", "por_dia": ind.faltas_dia_semana(per, u["id"] if u else None)}


def _evolucao(args):
    u, erro = _ubs_ou_erro(args)
    if erro:
        return erro
    return {"ubs": u["nome"] if u else "Rede inteira", "meses": ind.evolucao_mensal(u["id"] if u else None)}


def _jornada(per, args):
    u, erro = _ubs_ou_erro(args)
    return erro or {"ubs": u["nome"] if u else "Rede inteira", "etapas": ind.jornada_paciente(per, u["id"] if u else None)}


def _repetidas(per, args):
    u, erro = _ubs_ou_erro(args)
    return erro or {"ubs": u["nome"] if u else "Rede inteira", **ind.faltas_repetidas(per, u["id"] if u else None)}


def _serie_diaria(per, args):
    u, erro = _ubs_ou_erro(args)
    if erro:
        return erro
    dias = [d for d in ind.serie_diaria(per, u["id"] if u else None) if d["agendados"] or d["atendimentos"]]
    return {"ubs": u["nome"] if u else "Rede inteira", "dias": dias}


def _comparar(args):
    try:
        a = Periodo(date.fromisoformat(args["inicio_a"]), date.fromisoformat(args["fim_a"]))
        b = Periodo(date.fromisoformat(args["inicio_b"]), date.fromisoformat(args["fim_b"]))
    except (KeyError, ValueError):
        return {"erro": "Informe inicio_a, fim_a, inicio_b e fim_b no formato AAAA-MM-DD."}
    u, erro = _ubs_ou_erro(args)
    if erro:
        return erro
    saida = {"periodo_a": a.rotulo(), "periodo_b": b.rotulo(), "rede_a": ind.kpis(a), "rede_b": ind.kpis(b)}
    if u:
        linha = lambda per: next((r for r in ind.ranking_ubs(per) if r["ubs_id"] == u["id"]), None)  # noqa: E731
        saida.update(ubs=u["nome"], ubs_a=linha(a), ubs_b=linha(b))
    return saida


def _detalhe_exame(per, args):
    if not args.get("exame"):
        return {"erro": "Informe o parâmetro exame (sigla ou parte do nome)."}
    e = ind.exame_detalhe(args["exame"], per)
    return e or {"erro": f"Exame '{args['exame']}' não encontrado na base."}


def _mapa_ubs_dia(per, args):
    m = ind.mapa_calor_ubs_dia(per)
    return {"dias": m["dias"], "linhas": [{"ubs": l["ubs"], "taxas": [c["txt"] for c in l["celulas"]],
                                           "total": l["total"]["txt"]} for l in m["linhas"]]}


FERRAMENTAS = {
    "serie_diaria": {
        "descricao": "Dia a dia do período: agendados, faltas, taxa de falta, taxa de comparecimento, atendimentos "
                     "e exames no laboratório. Use para 'qual dia', picos e quedas. Rede ou uma UBS.",
        "parametros": {**_PERIODO, **_UBS},
        "fn": _com_periodo(_serie_diaria),
    },
    "comparar_periodos": {
        "descricao": "Compara dois períodos quaisquer lado a lado (ex.: julho × setembro): KPIs da rede e, se "
                     "informar a UBS, a linha da UBS em cada período.",
        "parametros": {"inicio_a": {"type": "string", "description": "Início do período A (AAAA-MM-DD)"},
                       "fim_a": {"type": "string", "description": "Fim do período A (AAAA-MM-DD)"},
                       "inicio_b": {"type": "string", "description": "Início do período B (AAAA-MM-DD)"},
                       "fim_b": {"type": "string", "description": "Fim do período B (AAAA-MM-DD)"}, **_UBS},
        "fn": _comparar,
    },
    "destaques_ubs": {
        "descricao": "UBS com maior comparecimento e as que mais reduziram faltas vs. o período anterior "
                     "(só UBS com 30+ agendamentos realizados).",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.destaques_ubs(p)),
    },
    "detalhe_exame": {
        "descricao": "Um exame específico (sigla ou parte do nome, ex.: 'hemograma', 'TSH'): quantidade no período, "
                     "% do total, setor, série mês a mês e UBS que mais pediram.",
        "parametros": {**_PERIODO, "exame": {"type": "string", "description": "Sigla ou parte do nome do exame"}},
        "fn": _com_periodo(_detalhe_exame),
    },
    "faltas_ubs_por_dia_semana": {
        "descricao": "Mapa de faltas por UBS × dia da semana (taxa em cada combinação). Mostra em que dia cada UBS falta mais.",
        "parametros": _PERIODO,
        "fn": _com_periodo(_mapa_ubs_dia),
    },
    "exames_por_ubs": {
        "descricao": "Atendimentos, exames e exames por atendimento de cada UBS de origem no período.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.exames_por_ubs(p)),
    },
    "numeros_do_periodo": {
        "descricao": "Curiosidades do período: dia mais movimentado, exame mais realizado, dia da semana com mais "
                     "faltas, exames por atendimento, UBS com mais agendamentos.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.numeros_periodo(p)),
    },
    "mostrar_grafico": {
        "descricao": "Mostra um gráfico ao gestor, abaixo da resposta (até 2 por resposta). O SISTEMA monta o "
                     "gráfico com dados do banco — você só escolhe o tipo. Use quando ver ajudar a entender.",
        "parametros": {"tipo": {"type": "string", "enum": list(GRAFICOS_TIPOS),
                                "description": "; ".join(f"{k}: {v}" for k, v in GRAFICOS_TIPOS.items())},
                       **_PERIODO, **_UBS,
                       "exame": {"type": "string", "description": "Só para exame_evolucao: sigla ou nome do exame"}},
        "fn": lambda a: {"ok": True},          # tratado pelo registro da conversa (llm._Registro)
    },
    "jornada_do_paciente": {
        "descricao": "Funil do comparecimento no período: agendados → com registro → compareceram → atendidos no "
                     "laboratório (Worklab em até 7 dias), com % sobre o total e perda em cada etapa. Rede ou uma UBS.",
        "parametros": {**_PERIODO, **_UBS},
        "fn": _com_periodo(_jornada),
    },
    "faltas_repetidas": {
        "descricao": "Quantos pacientes faltaram 1, 2 ou 3+ vezes no período e qual % das faltas vem de quem faltou "
                     "2+ vezes (só contagens agregadas). Útil para propor lembrete ativo. Rede ou uma UBS.",
        "parametros": {**_PERIODO, **_UBS},
        "fn": _com_periodo(_repetidas),
    },
    "cobertura_da_base": {
        "descricao": "Quais períodos e volumes existem na base. Use antes de concluir que algo não tem dados.",
        "parametros": {},
        "fn": lambda a: {**ind.cobertura(), "meses_disponiveis": ind.meses_disponiveis()},
    },
    "kpis_periodo": {
        "descricao": "KPIs executivos do período comparados ao período anterior: pacientes, atendimentos, exames, "
                     "agendamentos, faltas, taxa de falta/comparecimento, NCs, % atendimentos sem agendamento.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.kpis_comparados(p)),
    },
    "ranking_ubs": {
        "descricao": "Indicadores por UBS no período: agendados, realizados, faltas, taxa de falta (atual e anterior, "
                     "variação em p.p.), atendimentos, exames, taxa de recoleta e participação na demanda.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.ranking_ubs(p)),
    },
    "faltas_por_dia_semana": {
        "descricao": "Taxa de faltas por dia da semana no período, da rede ou de uma UBS.",
        "parametros": {**_PERIODO, **_UBS},
        "fn": _com_periodo(_faltas_dia),
    },
    "concentracao_por_horario": {
        "descricao": "Distribuição dos agendamentos por horário marcado no período.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.concentracao_horario(p)),
    },
    "evolucao_mensal": {
        "descricao": "Série mensal (todo o histórico) de agendamentos, faltas, taxa de falta, atendimentos, pacientes, "
                     "exames e NCs — da rede ou de uma UBS.",
        "parametros": _UBS,
        "fn": _evolucao,
    },
    "exames": {
        "descricao": "Exames mais realizados no período com variação vs. período anterior, e exames com maior "
                     "crescimento/queda relativa.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: {"top": ind.top_exames(p, 10), "variacao": ind.variacao_exames(p)[:5]
                                                                                    + ind.variacao_exames(p)[-5:]}),
    },
    "producao_por_setor": {
        "descricao": "Produção por setor no período: exames, valor, tempo médio de liberação (TAT), % no prazo, "
                     "recoletas, exames em análise; inclui a série mensal de TAT por setor.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: {"setores": ind.producao_setor(p), "tat_mensal": ind.tat_setor_mensal()}),
    },
    "cruzamento_agendamento_producao": {
        "descricao": "Cruza agendamento × comparecimento × produção: % dos que compareceram e foram atendidos no "
                     "laboratório, faltosos atendidos mesmo assim, atendimentos sem agendamento e sem UBS.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.cruzamento_agendamento_producao(p)),
    },
    "qualidade_nao_conformidades": {
        "descricao": "Não conformidades: total no período e no anterior, abertas, vencidas, tempo médio de "
                     "resolução, por categoria/etapa/setor, reincidências (90 dias) e status dos planos 5W2H.",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: ind.nc_resumo(p)),
    },
    "fila_de_triagem": {
        "descricao": "Situação atual da fila de triagem (tempo real): aguardando, > 24 h, urgentes, tempo médio até confirmar.",
        "parametros": {},
        "fn": lambda a: ind.triagem_resumo(),
    },
    "alertas_gerenciais": {
        "descricao": "Alertas detectados por regras estatísticas no período (absenteísmo, gargalos de TAT, "
                     "concentração de horário, recoletas, NCs vencidas/reincidentes, fila de triagem).",
        "parametros": _PERIODO,
        "fn": _com_periodo(lambda p, a: al.gerar_alertas(p)),
    },
}


def executar(nome: str, args: dict | None) -> dict:
    if nome not in FERRAMENTAS:
        return {"erro": f"Ferramenta desconhecida: {nome}"}
    return FERRAMENTAS[nome]["fn"](args or {})


def definicoes_anthropic() -> list[dict]:
    return [{"name": n, "description": f["descricao"],
             "input_schema": {"type": "object", "properties": f["parametros"]}}
            for n, f in FERRAMENTAS.items()]
