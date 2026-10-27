"""Modo OFFLINE do Analista: análise determinística sobre o catálogo de indicadores.

Funciona sem chave de API (a demonstração nunca quebra) e serve de referência
para o modo LLM: mesmas ferramentas, mesmos números, mesma estrutura
Problema → Evidência → Possível causa → Ação → Indicador.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from app.rotulos import ETAPA
from app.services import alertas as al
from app.services import indicadores as ind
from app.services.alertas import nome_ubs
from app.services.indicadores import Periodo
from etl.normalizacao import normalizar_texto


SEM_DADOS = "Não há dados suficientes para concluir."
MESES = {"JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7,
         "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12}
FORA_DO_ESCOPO = {
    "SATISFA": "satisfação dos pacientes", "ESTOQUE": "estoque de insumos", "CUSTO": "custos operacionais",
    "SALARI": "dados de equipe/salários", "FUNCIONARI": "escala de equipe", "ESCALA": "escala de equipe",
    "IDADE": "perfil etário (não vai para a camada analítica por privacidade)",
    "FAIXA ETARIA": "perfil etário", "SEXO": "perfil por sexo", "DIAGNOST": "diagnósticos clínicos",
    "RESULTADO DO EXAME": "resultados clínicos dos exames", "POSITIV": "resultados clínicos dos exames",
    "MOTIVO DA FALTA": "motivo individual das faltas", "TEMPO DE ESPERA": "tempo de espera na recepção",
}
HIPOTESES = {
    "Absenteísmo": "falta de confirmação prévia do agendamento, dificuldade de deslocamento ou mudança de rotina na UBS. "
                   "A base não registra o motivo da falta.",
    "Gargalo": "restrição de insumo/equipamento ou de capacidade do setor.",
    "Processo": "agenda configurada com um único horário de chegada para todos os pacientes.",
    "Qualidade": "condições de coleta/transporte ou causa raiz ainda não tratada.",
    "Triagem": "ausência de rotina de fechamento da fila ao fim do turno.",
    "Dados": "demanda espontânea ou paciente identificado de forma diferente entre os sistemas.",
    "Produção": "absenteísmo, paradas de equipamento ou falta de insumos.",
    "Demanda": "distribuição desigual de vagas entre as UBS.",
}


# ---------------------------------------------------------------------------
# formatação pt-BR
# ---------------------------------------------------------------------------
def n(x) -> str:
    return "—" if x is None else f"{x:,.0f}".replace(",", ".")


def pct(x) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def pp(x) -> str:
    return "—" if x is None else (f"{x:+.1f} p.p.".replace(".", ",", 1))


def var_pct(x) -> str:
    return "—" if x is None else f"{x:+.1f}%".replace(".", ",")


def tabela(cab: list[str], linhas: list[list]) -> str:
    out = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    out += ["| " + " | ".join(str(c) for c in l) + " |" for l in linhas]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# interpretação da pergunta
# ---------------------------------------------------------------------------
def extrair_periodo(q: str) -> Periodo | None:
    padrao = ind.periodo_padrao()
    if re.search(r"ULTIMOS? (3|TRES) MESES|TRIMESTRE", q):
        ini = (padrao.inicio - timedelta(days=1)).replace(day=1)
        ini = (ini - timedelta(days=1)).replace(day=1)
        return Periodo(ini, padrao.fim)
    if re.search(r"TODO O (PERIODO|HISTORICO)|TODOS OS MESES|DESDE O INICIO|HISTORICO COMPLETO", q):
        return ind.periodo_total()
    citados = sorted((mt.start(), m) for nome, m in MESES.items() for mt in re.finditer(rf"\b{nome}\b", q))
    if len(citados) >= 2 and re.search(r"\b(A|ATE)\b", q[citados[0][0]:citados[-1][0]]):
        meses = ind.meses_disponiveis()
        def _ano(m):
            anos = [int(x[:4]) for x in meses if int(x[5:]) == m]
            return max(anos) if anos else padrao.fim.year
        m1, m2 = citados[0][1], citados[-1][1]
        a2 = _ano(m2); a1 = a2 if m1 <= m2 else a2 - 1
        ini = date(a1, m1, 1)
        fim = (date(a2, m2, 1).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return Periodo(ini, fim)
    for nome, m in MESES.items():
        if re.search(rf"\b{nome}\b", q):
            ano = re.search(r"\b(20\d\d)\b", q)
            if ano:
                a = int(ano.group(1))
            else:
                anos = [int(x[:4]) for x in ind.meses_disponiveis() if int(x[5:]) == m]
                a = max(anos) if anos else padrao.fim.year
            ini = date(a, m, 1)
            fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            return Periodo(ini, fim)
    return None


def ubs_mencionada(q: str) -> dict | None:
    """Procura o nome de alguma UBS cadastrada dentro da pergunta (o mais longo vence)."""
    from app.db import consultar
    achadas = []
    for u in consultar("SELECT id, nome FROM ubs WHERE ativo"):
        nucleo = normalizar_texto(u["nome"])
        for pref in ("UBS ", "UNIDADE BASICA DE SAUDE "):
            nucleo = nucleo.removeprefix(pref)
        if len(nucleo) >= 4 and re.search(rf"\b{re.escape(nucleo)}\b", q):
            achadas.append((len(nucleo), u))
    return max(achadas, key=lambda x: x[0])[1] if achadas else None


def _tem(q: str, *termos) -> bool:
    return any(t in q for t in termos)


# ---------------------------------------------------------------------------
# blocos reutilizáveis
# ---------------------------------------------------------------------------
def bloco_acao(alerta: dict, extra_causa: str | None = None) -> str:
    causa = HIPOTESES.get(alerta["categoria"], "a investigar.")
    if extra_causa:
        causa += " " + extra_causa
    return (f"**Problema:** {alerta['titulo']}\n\n"
            f"**Evidência:** {' '.join(alerta['evidencias'])}\n\n"
            f"**Possível causa (a verificar):** {causa}\n\n"
            f"**Ação recomendada:** {' '.join(alerta['recomendacoes'][:2])}\n\n"
            f"**Indicador para acompanhar:** {alerta['indicador']}")


def _contexto_reagente(per: Periodo) -> str | None:
    for r in ind.nc_resumo(per)["reincidentes"]:
        if "REAGENTE" in normalizar_texto(r["categoria"]) or "EQUIPAMENTO" in normalizar_texto(r["categoria"]):
            return (f"Há {r['ocorrencias_90d']} não conformidades de '{r['categoria']}' nos últimos 90 dias, "
                    "o que é consistente com essa hipótese.")
    return None


# ---------------------------------------------------------------------------
# respostas por intenção
# ---------------------------------------------------------------------------
def r_ubs_atencao(per: Periodo, q: str):
    rk = [u for u in ind.ranking_ubs(per) if (u["realizados"] or 0) >= al.LIMIARES["min_realizados_ubs"]]
    if not rk:
        return SEM_DADOS + " Nenhuma UBS tem ao menos 30 agendamentos realizados no período.", ["ranking_ubs"]
    k = ind.kpis(per)
    pior = max(rk, key=lambda u: u["taxa_falta"] or 0)
    mais_faltas = max(rk, key=lambda u: u["faltas"])
    maior_alta = max((u for u in rk if u["variacao_pp"] is not None), key=lambda u: u["variacao_pp"], default=None)
    frase_taxa = (f"taxa de falta de **{pct(pior['taxa_falta'])}**, contra {pct(k['taxa_falta'])} da rede")
    if _tem(q, "NUMERO", "QUANTIDADE", "MAIS FALTAS", "MAIOR NUMERO"):
        txt = [f"**{nome_ubs(mais_faltas['ubs'])}** teve o maior número de faltas em {per.rotulo()}: "
               f"**{n(mais_faltas['faltas'])} faltas** em {n(mais_faltas['realizados'])} agendamentos realizados "
               f"({pct(mais_faltas['taxa_falta'])})."]
        if mais_faltas["ubs"] != pior["ubs"]:
            txt.append(f"Em proporção, o pior desempenho é da **{nome_ubs(pior['ubs'])}**: {frase_taxa}.")
    else:
        txt = [f"**{nome_ubs(pior['ubs'])}** teve o pior desempenho de comparecimento em {per.rotulo()}: {frase_taxa}."]
    txt.append("\n**Evidências**")
    txt.append(f"- {n(pior['faltas'])} faltas em {n(pior['realizados'])} agendamentos realizados.")
    if pior["taxa_falta_anterior"] is not None:
        txt.append(f"- Período anterior ({per.anterior().rotulo()}): {pct(pior['taxa_falta_anterior'])} "
                   f"({pp(pior['variacao_pp'])}).")
    if maior_alta and maior_alta["ubs"] != pior["ubs"] and maior_alta["variacao_pp"] > 0:
        txt.append(f"- Maior piora no período: **{nome_ubs(maior_alta['ubs'])}** "
                   f"({pct(maior_alta['taxa_falta_anterior'])} → {pct(maior_alta['taxa_falta'])}).")
    txt.append("\n**Ranking de faltas (UBS com ≥ 30 agendamentos realizados)**\n")
    txt.append(tabela(["UBS", "Realizados", "Faltas", "Taxa", "Var. vs anterior"],
                      [[nome_ubs(u["ubs"]), n(u["realizados"]), n(u["faltas"]), pct(u["taxa_falta"]), pp(u["variacao_pp"])]
                       for u in sorted(rk, key=lambda u: -(u["taxa_falta"] or 0))[:6]]))
    alertas_ubs = [a for a in al.gerar_alertas(per) if a["ubs"] and a["categoria"] in ("Absenteísmo", "Qualidade")]
    if alertas_ubs:
        txt.append("\n**Onde agir primeiro**\n")
        for a in alertas_ubs[:2]:
            txt.append(bloco_acao(a) + "\n")
    return "\n".join(txt), ["ranking_ubs", "kpis_periodo", "alertas_gerenciais"]


def r_explicar_faltas(per: Periodo, q: str):
    ant = per.anterior()
    k1, k0 = ind.kpis(per), ind.kpis(ant)
    if k1["taxa_falta"] is None or k0["taxa_falta"] is None:
        return SEM_DADOS + f" É preciso ter agendamentos realizados em {per.rotulo()} e em {ant.rotulo()}.", ["kpis_periodo"]
    d = k1["taxa_falta"] - k0["taxa_falta"]
    if d <= 0:
        return (f"As faltas **não aumentaram**: a taxa da rede foi de {pct(k0['taxa_falta'])} ({ant.rotulo()}) para "
                f"{pct(k1['taxa_falta'])} ({per.rotulo()}), variação de {pp(d)}."), ["kpis_periodo"]
    rk = ind.ranking_ubs(per)
    contrib = sorted(rk, key=lambda u: -((u["faltas"] or 0) - (u["faltas_anterior"] or 0)))
    delta_total = sum((u["faltas"] or 0) - (u["faltas_anterior"] or 0) for u in rk)
    ds1 = {r["dia_semana"]: r for r in ind.faltas_dia_semana(per)}
    ds0 = {r["dia_semana"]: r for r in ind.faltas_dia_semana(ant)}
    txt = [f"A taxa de faltas da rede subiu de **{pct(k0['taxa_falta'])}** para **{pct(k1['taxa_falta'])}** "
           f"({pp(d)}) entre {ant.rotulo()} e {per.rotulo()}. Os dados mostram **onde** o aumento se concentra; "
           "o **porquê** de cada falta não é registrado na base.",
           "\n**1. Quais UBS explicam o aumento** (variação no nº de faltas)\n",
           tabela(["UBS", "Faltas antes", "Faltas agora", "Δ faltas", "Taxa agora"],
                  [[nome_ubs(u["ubs"]), n(u["faltas_anterior"]), n(u["faltas"]),
                    f"{(u['faltas'] or 0) - (u['faltas_anterior'] or 0):+d}", pct(u["taxa_falta"])] for u in contrib[:5]])]
    top3 = sum((u["faltas"] or 0) - (u["faltas_anterior"] or 0) for u in contrib[:3])
    if delta_total > 0:
        txt.append(f"\nAs 3 primeiras respondem por {n(top3)} das {n(delta_total)} faltas a mais na rede.")
    txt.append("\n**2. Dia da semana**\n")
    txt.append(tabela(["Dia", "Taxa antes", "Taxa agora"],
                      [[dia, pct(ds0.get(dia, {}).get("taxa_falta")), pct(r["taxa_falta"])] for dia, r in ds1.items()]))
    pico = max(ds1.values(), key=lambda r: r["taxa_falta"] or 0)
    txt.append(f"\n{pico['dia_semana']} concentra a maior taxa ({pct(pico['taxa_falta'])}).")
    txt.append(f"\n**3. Volume:** {n(k0['realizados'])} → {n(k1['realizados'])} agendamentos realizados.")
    h = ind.concentracao_horario(per)
    if h:
        txt.append(f"\n**4. Horário:** {pct(h[0]['pct'])} dos pacientes foram agendados para as {h[0]['horario']}.")
    txt.append("\n**Possíveis causas (a verificar — não comprovadas pelos dados):** falta de confirmação prévia "
               "nas UBS listadas acima, mudança de rotina/equipe nessas unidades, dificuldade de acesso em "
               f"{pico['dia_semana'].lower()}s e fila na abertura pelo horário único de chegada.")
    txt.append("\n**Próximo passo sugerido:** ligar para uma amostra de faltosos das UBS do topo da tabela e "
               "registrar o motivo — isso transforma hipótese em dado.")
    return "\n".join(txt), ["kpis_periodo", "ranking_ubs", "faltas_por_dia_semana", "concentracao_por_horario"]


def r_evolucao(per: Periodo, q: str, ubs=None):
    meses = ind.evolucao_mensal(ubs["id"] if ubs else None)
    if len(meses) < 2:
        return SEM_DADOS + " É preciso ao menos dois meses de dados para avaliar evolução.", ["evolucao_mensal"]
    p, u = meses[0], meses[-1]
    alvo = f"da {nome_ubs(ubs['nome'])}" if ubs else "da rede"
    txt = [f"Evolução {alvo} de {p['mes']} a {u['mes']} ({len(meses)} meses):", ""]
    txt.append(tabela(["Mês", "Atendimentos", "Pacientes", "Exames", "Agendados", "Taxa de falta", "NCs"],
                      [[m["mes"], n(m["atendimentos"]), n(m["pacientes"]), n(m["exames"]), n(m["agendados"]),
                        pct(m["taxa_falta"]), n(m["nao_conformidades"])] for m in meses]))
    def v(a, b):
        return var_pct(100 * (b - a) / a) if a else "—"
    txt.append(f"\n- Atendimentos: {n(p['atendimentos'])} → {n(u['atendimentos'])} ({v(p['atendimentos'], u['atendimentos'])}); "
               f"último mês vs anterior: {v(meses[-2]['atendimentos'], u['atendimentos'])}.")
    txt.append(f"- Exames: {n(p['exames'])} → {n(u['exames'])} ({v(p['exames'], u['exames'])}).")
    if p["taxa_falta"] is not None and u["taxa_falta"] is not None:
        sobe = sum(1 for a, b in zip(meses, meses[1:]) if a["taxa_falta"] is not None and b["taxa_falta"] is not None
                   and b["taxa_falta"] > a["taxa_falta"])
        txt.append(f"- Taxa de falta: {pct(p['taxa_falta'])} → {pct(u['taxa_falta'])} ({pp(u['taxa_falta'] - p['taxa_falta'])}); "
                   f"subiu em {sobe} de {len(meses) - 1} transições mensais.")
    txt.append("\nObservação: meses têm quantidades diferentes de dias úteis — compare médias diárias antes de concluir queda de produção.")
    return "\n".join(txt), ["evolucao_mensal"]


def r_exames(per: Periodo, q: str):
    top = ind.top_exames(per, 10)
    if not top:
        return SEM_DADOS + f" Não há exames importados em {per.rotulo()}.", ["exames"]
    var = ind.variacao_exames(per)
    txt = [f"Em {per.rotulo()} os exames mais realizados foram **{top[0]['exame']}**, **{top[1]['exame']}** e "
           f"**{top[2]['exame']}** — juntos, {pct(sum(t['participacao'] for t in top[:3]))} do volume.", ""]
    txt.append(tabela(["Exame", "Setor", "Qtd", "Anterior", "Variação"],
                      [[t["exame"], t["setor"], n(t["quantidade"]), n(t["quantidade_anterior"]), var_pct(t["variacao_pct"])]
                       for t in top]))
    if var:
        cres, queda = var[0], var[-1]
        txt.append(f"\n- **Maior crescimento** (entre exames com ≥ 30 no período anterior): {cres['exame']} "
                   f"({n(cres['quantidade_anterior'])} → {n(cres['quantidade'])}, {var_pct(cres['variacao_pct'])}).")
        txt.append(f"- **Maior queda:** {queda['exame']} ({n(queda['quantidade_anterior'])} → {n(queda['quantidade'])}, "
                   f"{var_pct(queda['variacao_pct'])}).")
        txt.append("\nVariações de um mês para outro em exames de baixo volume costumam ser ruído; "
                   "confirme no gráfico de evolução antes de tratar como tendência.")
    return "\n".join(txt), ["exames"]


def r_tat(per: Periodo, q: str):
    setores = ind.producao_setor(per)
    com_tat = [s for s in setores if s["tat_medio_h"] is not None]
    if not com_tat:
        return SEM_DADOS + " A base do período não tem data/hora de liberação dos exames (TAT).", ["producao_por_setor"]
    pior = min(com_tat, key=lambda s: s["pct_no_prazo"] if s["pct_no_prazo"] is not None else 101)
    txt = [f"O setor com pior cumprimento de prazo em {per.rotulo()} é **{pior['setor']}**: "
           f"{pct(pior['pct_no_prazo'])} dos exames liberados no prazo, TAT médio de {n(pior['tat_medio_h'])} h.", ""]
    txt.append(tabela(["Setor", "Exames", "TAT médio (h)", "TAT p90 (h)", "% no prazo", "Recoletas", "Em análise"],
                      [[s["setor"], n(s["exames"]), n(s["tat_medio_h"]), n(s["tat_p90_h"]), pct(s["pct_no_prazo"]),
                        n(s["recoletas"]), n(s["em_analise"])] for s in setores]))
    serie = [r for r in ind.tat_setor_mensal() if r["setor"] == pior["setor"]]
    if len(serie) >= 2:
        txt.append(f"\n**Histórico de {pior['setor']}:** " +
                   " · ".join(f"{r['mes']}: {n(r['tat_medio_h'])} h ({pct(r['pct_no_prazo'])} no prazo)" for r in serie))
    ctx = _contexto_reagente(per)
    if ctx:
        txt.append(f"\n{ctx}")
    return "\n".join(txt), ["producao_por_setor", "qualidade_nao_conformidades"]


def r_gargalos(per: Periodo, q: str):
    alertas = [a for a in al.gerar_alertas(per) if a["categoria"] in
               ("Gargalo", "Processo", "Triagem", "Absenteísmo", "Qualidade", "Produção")]
    if not alertas:
        return f"Nenhum gargalo ultrapassou os limiares de alerta em {per.rotulo()}.", ["alertas_gerenciais"]
    k = ind.kpis(per)
    cz = ind.cruzamento_agendamento_producao(per)
    txt = [f"Principais gargalos identificados em {per.rotulo()}, do mais crítico ao menos crítico:", ""]
    vistos = set()
    for a in alertas:
        if a["categoria"] in vistos and a["categoria"] != "Gargalo":
            continue
        vistos.add(a["categoria"])
        txt.append(f"- **[{a['categoria']}] {a['titulo']}** — {a['evidencias'][0]}")
    txt.append(f"\n**Capacidade perdida por absenteísmo:** {n(k['faltas'])} vagas ociosas ({pct(k['taxa_falta'])} dos "
               "agendamentos realizados).")
    txt.append(f"**Retrabalho:** recoletas aparecem no ranking por UBS; {n(cz['sem_agendamento'])} atendimentos "
               f"({pct(cz['pct_atendimentos_sem_agendamento'])}) chegaram sem agendamento correspondente.")
    ctx = _contexto_reagente(per)
    if ctx:
        txt.append(f"\n{ctx}")
    return "\n".join(txt), ["alertas_gerenciais", "kpis_periodo", "cruzamento_agendamento_producao"]


def r_alertas(per: Periodo, q: str):
    alertas = al.gerar_alertas(per)
    if not alertas:
        return f"Nenhum indicador ultrapassou os limiares de alerta em {per.rotulo()}.", ["alertas_gerenciais"]
    icone = {"alta": "🔴", "media": "🟠", "baixa": "🔵"}
    txt = [f"{len(alertas)} pontos de atenção em {per.rotulo()} "
           f"({sum(a['severidade'] == 'alta' for a in alertas)} de severidade alta):", ""]
    for a in alertas:
        txt.append(f"- {icone[a['severidade']]} **{a['titulo']}** — {a['evidencias'][0]}")
    return "\n".join(txt), ["alertas_gerenciais"]


def r_prioridades(per: Periodo, q: str):
    alertas = al.gerar_alertas(per)
    if not alertas:
        return f"Nenhum indicador ultrapassou os limiares de alerta em {per.rotulo()} — não há prioridade evidente nos dados.", ["alertas_gerenciais"]
    escolhidos, cats = [], set()
    for a in alertas:
        chave = (a["categoria"], a["ubs"] is None)
        if chave in cats:
            continue
        cats.add(chave)
        escolhidos.append(a)
        if len(escolhidos) == 4:
            break
    txt = [f"Com base nos dados de {per.rotulo()}, estas são as prioridades em ordem de impacto:", ""]
    ctx = _contexto_reagente(per)
    for i, a in enumerate(escolhidos, 1):
        txt.append(f"#### Prioridade {i}\n")
        txt.append(bloco_acao(a, ctx if a["categoria"] == "Gargalo" else None) + "\n")
    return "\n".join(txt), ["alertas_gerenciais", "qualidade_nao_conformidades"]


def r_qualidade(per: Periodo, q: str):
    r = ind.nc_resumo(per)
    g = r["geral"]
    txt = [f"Em {per.rotulo()} foram registradas **{n(g['no_periodo'])} não conformidades** "
           f"(período anterior: {n(g['periodo_anterior'])}). Hoje há {n(g['abertas'])} em aberto, "
           f"{n(g['vencidas'])} com prazo vencido.", ""]
    if r["por_categoria"]:
        txt.append(tabela(["Categoria", "Etapa", "Qtd", "Em aberto"],
                          [[c["categoria"], ETAPA.get(c["etapa"], c["etapa"]), n(c["qtd"]), n(c["abertas"])]
                           for c in r["por_categoria"][:8]]))
    if r["reincidentes"]:
        txt.append("\n**Reincidências (≥ 3 nos últimos 90 dias):** " +
                   "; ".join(f"{x['categoria']} ({x['ocorrencias_90d']})" for x in r["reincidentes"]))
    p = r["planos"]
    txt.append(f"\n**Planos de ação 5W2H:** {n(p['total'])} no total, {n(p['concluidas'])} concluídos, "
               f"{n(p['abertas'])} em andamento, {n(p['atrasadas'])} atrasados.")
    if g["tempo_medio_resolucao_dias"] is not None:
        txt.append(f"**Tempo médio de resolução** das NCs concluídas no período: {n(g['tempo_medio_resolucao_dias'])} dias.")
    return "\n".join(txt), ["qualidade_nao_conformidades"]


def r_triagem(per: Periodo, q: str):
    t = ind.triagem_resumo()
    return (f"Fila de triagem agora: **{n(t['aguardando'])} aguardando**, {n(t['aguardando_mais_24h'])} há mais de 24 h "
            f"e {n(t['urgentes_aguardando'])} urgente(s). Hoje entraram {n(t['hoje'])} triagens "
            f"({n(t['confirmadas_hoje'])} já confirmadas). Tempo médio até a confirmação nos últimos 7 dias: "
            f"{n(t['minutos_medios_ate_confirmar'])} min."), ["fila_de_triagem"]


def r_perfil_ubs(per: Periodo, q: str, u: dict):
    rk = {x["ubs_id"]: x for x in ind.ranking_ubs(per)}
    x = rk.get(u["id"])
    if not x:
        return SEM_DADOS + f" Não há registros da {nome_ubs(u['nome'])} em {per.rotulo()}.", ["ranking_ubs"]
    k = ind.kpis(per)
    ordem = sorted([r for r in rk.values() if r["taxa_falta"] is not None], key=lambda r: -r["taxa_falta"])
    pos = next((i for i, r in enumerate(ordem, 1) if r["ubs_id"] == u["id"]), None)
    txt = [f"**{nome_ubs(u['nome'])}** em {per.rotulo()}:", "",
           f"- Agendamentos: {n(x['agendados'])} ({pct(x['participacao_demanda'])} da demanda da rede)",
           f"- Taxa de falta: **{pct(x['taxa_falta'])}** (rede: {pct(k['taxa_falta'])}; anterior: "
           f"{pct(x['taxa_falta_anterior'])}, {pp(x['variacao_pp'])}) — {pos}º maior de {len(ordem)} UBS",
           f"- Atendimentos no laboratório: {n(x['atendimentos'])} · Exames: {n(x['exames'])} · Recoleta: {pct(x['taxa_recoleta'])}"]
    ds = ind.faltas_dia_semana(per, u["id"])
    if ds:
        pico = max(ds, key=lambda r: r["taxa_falta"] or 0)
        txt.append(f"- Dia com mais faltas: {pico['dia_semana']} ({pct(pico['taxa_falta'])})")
    hist = [m for m in ind.evolucao_mensal(u["id"]) if m["taxa_falta"] is not None]
    if len(hist) >= 2:
        txt.append("- Histórico da taxa de falta: " + " → ".join(f"{m['mes'][5:]}/{m['mes'][2:4]} {pct(m['taxa_falta'])}" for m in hist))
    alertas = [a for a in al.gerar_alertas(per) if a["ubs"] == u["nome"]]
    if alertas:
        txt.append("\n**Alertas desta UBS**\n")
        for a in alertas:
            txt.append(bloco_acao(a) + "\n")
    return "\n".join(txt), ["ranking_ubs", "faltas_por_dia_semana", "evolucao_mensal", "alertas_gerenciais"]


def r_resumo(per: Periodo, q: str):
    k = ind.kpis_comparados(per)
    a, v = k["atual"], k["variacao"]
    def var(c):
        x = v.get(c)
        return "" if not x else f" ({pp(x['valor']) if x['tipo'] == 'pp' else var_pct(x['valor'])} vs anterior)"
    alertas = al.gerar_alertas(per)
    txt = [f"**Visão geral — {per.rotulo()}**", "",
           f"- Pacientes atendidos: {n(a['pacientes'])}{var('pacientes')}",
           f"- Atendimentos: {n(a['atendimentos'])}{var('atendimentos')} · Exames: {n(a['exames'])}{var('exames')}",
           f"- Agendamentos: {n(a['agendamentos'])} · Comparecimento: {pct(a['taxa_comparecimento'])} · "
           f"Faltas: {pct(a['taxa_falta'])}{var('taxa_falta')}",
           f"- Não conformidades no período: {n(a['nc_periodo'])} · em aberto: {n(a['nc_abertas'])} · vencidas: {n(a['nc_vencidas'])}"]
    if alertas:
        txt.append("\n**Principais pontos de atenção:** " + "; ".join(x["titulo"] for x in alertas[:3]) + ".")
    return "\n".join(txt), ["kpis_periodo", "alertas_gerenciais"]


def r_comparecimento(per: Periodo, q: str):
    k = ind.kpis_comparados(per)
    a, b = k["atual"], k["anterior"]
    rk = [u for u in ind.ranking_ubs(per) if (u["realizados"] or 0) >= 30]
    txt = [f"Em {per.rotulo()} a taxa de comparecimento da rede foi **{pct(a['taxa_comparecimento'])}** "
           f"({n(a['compareceram'])} de {n(a['realizados'])} agendamentos realizados) e a de faltas, "
           f"**{pct(a['taxa_falta'])}**. No período anterior: {pct(b['taxa_comparecimento'])} / {pct(b['taxa_falta'])}."]
    if rk:
        melhor = min(rk, key=lambda u: u["taxa_falta"])
        pior = max(rk, key=lambda u: u["taxa_falta"])
        txt.append(f"\n- Melhor comparecimento: {nome_ubs(melhor['ubs'])} ({pct(100 - melhor['taxa_falta'])}).")
        txt.append(f"- Pior comparecimento: {nome_ubs(pior['ubs'])} ({pct(100 - pior['taxa_falta'])}).")
    cz = ind.cruzamento_agendamento_producao(per)
    txt.append(f"- Dos que compareceram, {pct(cz['pct_comparecidos_com_atendimento'])} têm atendimento registrado no "
               "laboratório em até 7 dias (cruzamento agendamento × Worklab).")
    return "\n".join(txt), ["kpis_periodo", "ranking_ubs", "cruzamento_agendamento_producao"]


def r_melhor_comparecimento(per: Periodo, q: str):
    d = ind.destaques_ubs(per)
    if not d["comparecimento"]:
        return f"{SEM_DADOS} Nenhuma UBS teve 30+ agendamentos realizados em {per.rotulo()}.", ["ranking_ubs"]
    txt = [f"**UBS com melhor comparecimento — {per.rotulo()}** (UBS com 30+ agendamentos realizados)", ""]
    for i, r in enumerate(d["comparecimento"], 1):
        txt.append(f"{i}. **{nome_ubs(r['ubs'])}** — {pct(r['taxa_comparecimento'])} "
                   f"({n(r['compareceram'])} de {n(r['realizados'])})")
    if d["melhoraram"]:
        r = d["melhoraram"][0]
        txt.append(f"\nMaior redução de faltas vs. período anterior: **{nome_ubs(r['ubs'])}** "
                   f"({pct(r['taxa_falta_anterior'])} → {pct(r['taxa_falta'])}). Vale entender o que essa UBS fez "
                   "de diferente (possível causa a verificar: confirmação prévia, organização da agenda).")
    return "\n".join(txt), ["ranking_ubs"]


def r_presencas(per: Periodo, q: str):
    """UBS que mais trouxeram gente: ordena pelo NÚMERO de presenças (não pelo percentual)."""
    rk = sorted([r for r in ind.ranking_ubs(per) if r["compareceram"]], key=lambda r: -r["compareceram"])
    if not rk:
        return f"{SEM_DADOS} Não há comparecimentos registrados em {per.rotulo()}.", ["presencas_ubs"]
    total = sum(r["compareceram"] for r in rk)
    txt = [f"**UBS com mais presenças — {per.rotulo()}** (número de pessoas que compareceram)", ""]
    for i, r in enumerate(rk[:10], 1):
        txt.append(f"{i}. **{nome_ubs(r['ubs'])}** — {n(r['compareceram'])} presenças "
                   f"({pct(100.0 * r['compareceram'] / total)} do total · comparecimento de {pct(r['taxa_comparecimento'])})")
    top3 = sum(r["compareceram"] for r in rk[:3])
    txt.append(f"\nNa rede foram **{n(total)} presenças** em {n(len(rk))} UBS; as 3 primeiras somam "
               f"{pct(100.0 * top3 / total)}. Volume alto não significa bom comparecimento: compare com a taxa de cada UBS.")
    return "\n".join(txt), ["presencas_ubs"]


def r_jornada(per: Periodo, q: str):
    j = ind.jornada_paciente(per)
    if not j:
        return f"{SEM_DADOS} Não há agendamentos em {per.rotulo()}.", ["jornada_do_paciente"]
    txt = [f"**Jornada do paciente — {per.rotulo()}**", ""]
    for i, e in enumerate(j):
        perda = f" · perda de {n(j[i - 1]['quantidade'] - e['quantidade'])} na etapa" if i else ""
        txt.append(f"- {e['etapa']}: **{n(e['quantidade'])}** ({pct(e['pct_total'])} do agendado){perda}")
    maior = max(range(1, len(j)), key=lambda i: j[i - 1]["quantidade"] - j[i]["quantidade"])
    txt.append(f"\nA maior perda está na etapa **{j[maior]['etapa'].lower()}**. Atendidos no laboratório = compareceu e "
               "tem atendimento no Worklab em até 7 dias; diferenças também podem vir de identificação diferente entre "
               "os sistemas (a verificar).")
    return "\n".join(txt), ["jornada_do_paciente"]


def r_repetidas(per: Periodo, q: str):
    r = ind.faltas_repetidas(per)
    if not r["faltas"]:
        return f"Não houve faltas registradas em {per.rotulo()}.", ["faltas_repetidas"]
    fx = {f["faixa"]: f for f in r["faixas"]}
    txt = [f"Em {per.rotulo()}, **{n(r['pacientes_com_falta'])}** pacientes faltaram ao menos uma vez "
           f"({n(r['faltas'])} faltas).", "",
           f"- 1 falta: {n(fx['1 falta']['pacientes'])} pacientes",
           f"- 2 faltas: {n(fx['2 faltas']['pacientes'])} pacientes",
           f"- 3 ou mais: {n(fx['3 ou mais']['pacientes'])} pacientes",
           f"\nQuem faltou 2+ vezes ({n(r['pacientes_repetidos'])} pacientes) responde por "
           f"**{pct(r['pct_faltas_de_repetidos'])}** das faltas.",
           "\n**Ação recomendada:** lembrete ativo (ligação ou mensagem) para quem já faltou antes. "
           "**Indicador para acompanhar:** % das faltas vindas de pacientes recorrentes."]
    return "\n".join(txt), ["faltas_repetidas"]


def r_relatorio(per: Periodo, q: str):
    """Relatório executivo: resumo + destaques positivos + prioridades (mesmos números do painel)."""
    resumo, f1 = r_resumo(per, q)
    melhores, f2 = r_melhor_comparecimento(per, q)
    jornada, f3 = r_jornada(per, q)
    prio, f4 = r_prioridades(per, q)
    txt = [f"### Relatório executivo — {per.rotulo()}", "", "#### Resumo e principais números", resumo, "",
           "#### Jornada do paciente", jornada, "", "#### Destaques positivos", melhores, "",
           "#### Pontos de atenção e próximos passos", prio]
    return "\n".join(txt), sorted(set(f1 + f2 + f3 + f4))


CAPACIDADES = ("UBS com pior desempenho ou que merece atenção · por que as faltas aumentaram · evolução dos "
               "atendimentos · exames mais realizados e em crescimento · gargalos · tempo de liberação por setor · "
               "não conformidades · fila de triagem · o que o gestor deve priorizar · perfil de uma UBS específica · "
               "UBS com melhor comparecimento · jornada do paciente · pacientes que faltam repetidamente · "
               "relatório executivo do mês")


def responder(pergunta: str) -> dict:
    q = normalizar_texto(pergunta)
    per = extrair_periodo(q) or ind.periodo_padrao()
    base = ind.base_analisada(per)

    if _tem(q, "RELATORIO"):          # pedido de relatório: sempre dentro do escopo
        texto, ferr = r_relatorio(per, q)
        return {"resposta": texto, "ferramentas": ferr, "periodo": per}
    for termo, assunto in FORA_DO_ESCOPO.items():
        if re.search(rf"\b{termo}", q):
            return {"resposta": f"{SEM_DADOS} A base do LACEN Intelligence não contém **{assunto}**. "
                                f"Os dados disponíveis cobrem agendamentos, comparecimento, produção laboratorial "
                                f"(atendimentos, exames, prazos), triagem e não conformidades.",
                    "ferramentas": [], "periodo": per}
    if base["registros"] == 0 and not _tem(q, "TRIAGEM", "FILA", "EVOLU"):
        cob = ind.meses_disponiveis()
        return {"resposta": f"{SEM_DADOS} Não há registros em {per.rotulo()}. "
                            f"Meses com dados: {', '.join(cob) if cob else 'nenhum — importe arquivos primeiro'}.",
                "ferramentas": ["cobertura_da_base"], "periodo": per}

    ubs = ubs_mencionada(q)

    if _tem(q, "NAO CONFORMIDADE", "QUALIDADE", "5W2H", "REINCID", " NC", "NCS"):
        texto, ferr = r_qualidade(per, q)
    elif _tem(q, "TRIAGEM", "FILA"):
        texto, ferr = r_triagem(per, q)
    elif ubs and _tem(q, "EVOLU"):
        texto, ferr = r_evolucao(per, q, ubs)
    elif ubs:
        texto, ferr = r_perfil_ubs(per, q, ubs)
    elif _tem(q, "JORNADA", "FUNIL"):
        texto, ferr = r_jornada(per, q)
    elif _tem(q, "REPETID", "RECORRENT", "MAIS DE UMA VEZ", "REINCIDENT") and _tem(q, "FALT"):
        texto, ferr = r_repetidas(per, q)
    elif _tem(q, "PRESENC", "MAIS VEIO", "MAIS VIERAM", "MAIS COMPARECERAM", "MAIS GENTE", "MAIS PACIENTES"):
        texto, ferr = r_presencas(per, q)
    elif _tem(q, "MELHOR") and _tem(q, "COMPAREC"):
        texto, ferr = r_melhor_comparecimento(per, q)
    elif _tem(q, "POR QUE", "PORQUE", "MOTIVO", "CAUSA", "EXPLICA") and _tem(q, "FALTA", "ABSENTE"):
        texto, ferr = r_explicar_faltas(per, q)
    elif _tem(q, "PRIORIZ", "PRIORIDADE", "DEVERIA FAZER", "O QUE FAZER", "RECOMEND", "O QUE O GESTOR"):
        texto, ferr = r_prioridades(per, q)
    elif _tem(q, "GARGALO"):
        texto, ferr = r_gargalos(per, q)
    elif _tem(q, "PRAZO", "LIBERACAO", "TAT", "DEMORA", "ATRASO", "SETOR"):
        texto, ferr = r_tat(per, q)
    elif _tem(q, "PIOR", "MELHOR", "MERECE", "RANKING", "MAIOR NUMERO DE FALTAS", "MAIS FALTAS") or \
            (_tem(q, "ATENCAO") and _tem(q, "UBS", "UNIDADE")):
        texto, ferr = r_ubs_atencao(per, q)
    elif _tem(q, "PROBLEMA", "ALERTA", "ATENCAO", "RISCO"):
        texto, ferr = r_alertas(per, q)
    elif _tem(q, "EXAME"):
        texto, ferr = r_exames(per, q)
    elif _tem(q, "EVOLU", "TENDENCIA", "ATENDIMENTO", "PRODUCAO", "CRESC"):
        texto, ferr = r_evolucao(per, q)
    elif _tem(q, "COMPAREC", "FALTA", "ABSENTE", "AGENDAMENTO"):
        texto, ferr = r_comparecimento(per, q)
    elif _tem(q, "RESUMO", "VISAO GERAL", "COMO ESTAMOS", "SITUACAO", "PANORAMA", "INDICADORES"):
        texto, ferr = r_resumo(per, q)
    else:
        texto, ferr = (f"Não consegui relacionar a pergunta aos indicadores disponíveis. "
                       f"No modo offline sei responder sobre: {CAPACIDADES}."), []
    return {"resposta": texto, "ferramentas": ferr, "periodo": per}
