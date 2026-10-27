"""Motor de alertas gerenciais — regras determinísticas e auditáveis.

Decisão de projeto: quem DETECTA o problema é a regra (reprodutível, com limiar
explícito); a IA só ajuda a explicar e priorizar. Assim um alerta nunca é
"alucinado" e sempre pode ser conferido no painel.

Cada limiar está em LIMIARES para ser ajustado pela gestão.
"""
from __future__ import annotations

import math

from app.services import indicadores as ind
from app.services.indicadores import Periodo

LIMIARES = {
    "min_realizados_ubs": 30,          # amostra mínima para avaliar uma UBS
    "alta_faltas_pp_media": 8.0,       # aumento de faltas (p.p.) que gera alerta médio
    "alta_faltas_pp_alta": 12.0,       # … e alto
    "faltas_vs_rede": 1.5,             # UBS com faltas ≥ 1,5× a média da rede
    "concentracao_horario_pct": 80.0,  # % dos agendamentos num único horário
    "queda_producao_pct": -15.0,       # queda de exames/dia vs período anterior
    "prazo_minimo_pct": 80.0,          # % de exames liberados no prazo
    "recoleta_vs_rede": 2.0,
    "recoleta_minima_pct": 2.0,
    "demanda_concentrada_pct": 12.0,
    "sem_agendamento_pct": 25.0,
}
PESO = {"alta": 0, "media": 1, "baixa": 2}
Z_CRITICO = 1.96  # 95% de confiança


def _f(x) -> str:
    return f"{x:.1f}".replace(".", ",")


def _f0(x) -> str:
    return f"{x:,.0f}".replace(",", ".")


def nome_ubs(nome: str) -> str:
    """'UBS VILA NOVA' -> 'UBS Vila Nova' (legível sem perder a sigla)."""
    t = nome.title()
    for sigla in ("Ubs ", "Cta ", "Sae "):
        t = t.replace(sigla, sigla.upper())
    for p in (" Da ", " Das ", " Do ", " Dos ", " De ", " E "):
        t = t.replace(p, p.lower())
    return t


def diferenca_significativa(f1: int, n1: int, f0: int, n0: int) -> bool:
    """Teste z de duas proporções (aumento de f1/n1 em relação a f0/n0)."""
    if min(n1, n0) == 0:
        return False
    p = (f1 + f0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0)) if 0 < p < 1 else 0
    return se > 0 and ((f1 / n1) - (f0 / n0)) / se >= Z_CRITICO


def acima_da_referencia(f: int, n: int, p_ref: float) -> bool:
    """Teste z de uma proporção contra a taxa da rede."""
    if n == 0 or not 0 < p_ref < 1:
        return False
    return ((f / n) - p_ref) / math.sqrt(p_ref * (1 - p_ref) / n) >= Z_CRITICO


def _alerta(sev, categoria, titulo, evidencias, recomendacoes, indicador, ubs=None):
    return {"severidade": sev, "categoria": categoria, "titulo": titulo, "evidencias": evidencias,
            "recomendacoes": recomendacoes, "indicador": indicador, "ubs": ubs}


def gerar_alertas(per: Periodo) -> list[dict]:
    L = LIMIARES
    alertas: list[dict] = []
    ant = per.anterior()
    k = ind.kpis_comparados(per)
    atual, anterior = k["atual"], k["anterior"]

    if not atual["realizados"] and not atual["atendimentos"]:
        return [_alerta("baixa", "Dados", "Não há dados suficientes para o período selecionado",
                        [f"Nenhum agendamento realizado ou atendimento entre {per.rotulo()}."],
                        ["Importe os arquivos do período em Importação de dados."], "Cobertura da base")]

    # ---------------- Agendamento / absenteísmo ----------------
    ranking = ind.ranking_ubs(per)
    taxa_rede = atual["taxa_falta"] or 0
    for u in ranking:
        if (u["realizados"] or 0) < L["min_realizados_ubs"]:
            continue
        var = u["variacao_pp"]
        if (var is not None and var >= L["alta_faltas_pp_media"]
                and diferenca_significativa(u["faltas"], u["realizados"], u["faltas_anterior"], u["realizados_anterior"])):
            sev = "alta" if var >= L["alta_faltas_pp_alta"] else "media"
            alertas.append(_alerta(
                sev, "Absenteísmo", f"Faltas da {nome_ubs(u['ubs'])} subiram {_f(var)} p.p.",
                [f"Taxa de faltas: {_f(u['taxa_falta_anterior'])}% ({ant.rotulo()}) → {_f(u['taxa_falta'])}% ({per.rotulo()}).",
                 f"{u['faltas']} faltas em {u['realizados']} agendamentos realizados.",
                 f"Média da rede no período: {_f(taxa_rede)}%.",
                 "Diferença estatisticamente significativa (teste z, 95%)."],
                ["Verificar concentração das faltas por dia da semana e horário.",
                 "Comparar o volume agendado com o histórico da UBS.",
                 "Checar se houve mudança de rotina na UBS (equipe, confirmação prévia, transporte)."],
                "Taxa de faltas mensal da UBS", u["ubs"]))
        elif (taxa_rede and u["taxa_falta"] and u["taxa_falta"] >= L["faltas_vs_rede"] * taxa_rede
              and acima_da_referencia(u["faltas"], u["realizados"], taxa_rede / 100)):
            alertas.append(_alerta(
                "media", "Absenteísmo", f"{nome_ubs(u['ubs'])} com faltas {_f(u['taxa_falta'] / taxa_rede)}× acima da rede",
                [f"Taxa de faltas {_f(u['taxa_falta'])}% contra {_f(taxa_rede)}% da rede ({per.rotulo()}).",
                 f"No período anterior: {_f(u['taxa_falta_anterior'] or 0)}% — problema persistente." if
                 (u['taxa_falta_anterior'] or 0) >= L["faltas_vs_rede"] * taxa_rede else
                 f"No período anterior: {_f(u['taxa_falta_anterior'] or 0)}%."],
                ["Avaliar confirmação ativa do agendamento (ligação/WhatsApp) 24–48h antes.",
                 "Reavaliar a oferta de vagas da UBS frente ao comparecimento real (overbooking controlado)."],
                "Taxa de faltas da UBS vs. média da rede", u["ubs"]))

    if atual["taxa_falta"] is not None and anterior["taxa_falta"] is not None:
        d = atual["taxa_falta"] - anterior["taxa_falta"]
        if d >= 3:
            alertas.append(_alerta("media", "Absenteísmo", f"Absenteísmo da rede subiu {_f(d)} p.p.",
                                   [f"{_f(anterior['taxa_falta'])}% → {_f(atual['taxa_falta'])}% ({k['periodo_anterior']} → {per.rotulo()})."],
                                   ["Identificar quais UBS explicam o aumento (ranking de variação)."],
                                   "Taxa de faltas da rede"))

    horarios = ind.concentracao_horario(per)
    if horarios and float(horarios[0]["pct"]) >= L["concentracao_horario_pct"]:
        h = horarios[0]
        unico = float(h["pct"]) >= 90
        titulo = (f"Agendamento em horário único: {_f0(float(h['pct']))}% dos pacientes chegam às {h['horario']}" if unico
                  else f"{_f0(float(h['pct']))}% dos pacientes agendados para as {h['horario']}")
        alertas.append(_alerta(
            "media", "Processo", titulo,
            [f"{_f0(h['agendamentos'])} de {_f0(sum(x['agendamentos'] for x in horarios))} agendamentos no mesmo horário ({per.rotulo()}).",
             "Todos chegando juntos concentram a fila na abertura e o pico de trabalho da coleta nas primeiras horas."],
            ["Testar a chegada escalonada (ex.: blocos de 30 min entre 07:00 e 09:00) em uma UBS piloto.",
             "Medir o tempo de espera na recepção antes/depois da mudança."],
            "Distribuição de agendamentos por horário"))

    top = max(ranking, key=lambda u: u["participacao_demanda"] or 0, default=None)
    if top and (top["participacao_demanda"] or 0) >= L["demanda_concentrada_pct"]:
        alertas.append(_alerta("baixa", "Demanda", f"{nome_ubs(top['ubs'])} concentra {_f(top['participacao_demanda'])}% da demanda",
                               [f"{top['agendados']} agendamentos no período."],
                               ["Verificar se a capacidade de coleta/transporte da UBS acompanha a demanda."],
                               "Participação da UBS na demanda", top["ubs"]))

    # ---------------- Produção ----------------
    def por_dia(kp):
        return kp["exames"] / kp["atendimentos"] * kp["media_atendimentos_dia"] if kp["atendimentos"] and kp["media_atendimentos_dia"] else None
    e1, e0 = por_dia(atual), por_dia(anterior)
    if e1 and e0:
        var = 100 * (e1 - e0) / e0
        if var <= L["queda_producao_pct"]:
            alertas.append(_alerta("alta", "Produção", f"Produção diária de exames caiu {_f0(abs(var))}%",
                                   [f"Média de {_f0(e0)} exames/dia → {_f0(e1)} exames/dia.",
                                    f"Atendimentos: {anterior['atendimentos']} → {atual['atendimentos']}."],
                                   ["Cruzar com absenteísmo e com paradas de equipamento/falta de insumo."],
                                   "Exames por dia útil"))

    for s in ind.producao_setor(per):
        if s["pct_no_prazo"] is not None and s["pct_no_prazo"] < L["prazo_minimo_pct"]:
            sev = "alta" if s["pct_no_prazo"] < 60 else "media"
            alertas.append(_alerta(
                sev, "Gargalo", f"{s['setor']}: só {_f0(s['pct_no_prazo'])}% dos exames liberados no prazo",
                [f"Tempo médio de liberação {_f0(s['tat_medio_h'])} h (90% até {_f0(s['tat_p90_h'])} h) em {per.rotulo()}.",
                 f"{_f0(s['exames'])} exames no setor; {s['em_analise']} ainda em análise."],
                ["Verificar estoque de reagentes e disponibilidade de equipamento do setor.",
                 "Checar não conformidades abertas do setor.",
                 "Priorizar a fila por data de coleta."],
                "% de exames liberados no prazo (TAT)"))

    rec_validos = [u for u in ranking if u["taxa_recoleta"] is not None and (u["atendimentos"] or 0) >= 30]
    if rec_validos:
        media_rec = sum(u["taxa_recoleta"] for u in rec_validos) / len(rec_validos)
        for u in rec_validos:
            if u["taxa_recoleta"] >= max(L["recoleta_minima_pct"], L["recoleta_vs_rede"] * media_rec):
                alertas.append(_alerta("media", "Qualidade", f"Recoletas elevadas na {nome_ubs(u['ubs'])}",
                                       [f"{_f(u['taxa_recoleta'])}% dos exames precisaram de recoleta (média: {_f(media_rec)}%)."],
                                       ["Auditar coleta e transporte da UBS (hemólise, temperatura, tempo de trânsito)."],
                                       "Taxa de recoleta por UBS", u["ubs"]))

    # ---------------- Qualidade ----------------
    nc = ind.nc_resumo(per)
    g = nc["geral"]
    if g["vencidas"]:
        alertas.append(_alerta("media", "Qualidade", f"{g['vencidas']} não conformidade(s) com prazo vencido",
                               [f"{g['abertas']} não conformidades em aberto no total."],
                               ["Revisar responsáveis e prazos na reunião de qualidade."],
                               "NCs vencidas"))
    if g["periodo_anterior"] and g["no_periodo"] >= 5 and g["no_periodo"] >= 1.5 * g["periodo_anterior"]:
        alertas.append(_alerta("media", "Qualidade", "Aumento de não conformidades",
                               [f"{g['periodo_anterior']} → {g['no_periodo']} registros ({k['periodo_anterior']} → {per.rotulo()})."],
                               ["Ver quais categorias concentram o aumento."], "NCs por período"))
    for r in nc["reincidentes"][:3]:
        alertas.append(_alerta("media", "Qualidade", f"Reincidência: {r['categoria']}",
                               [f"{r['ocorrencias_90d']} ocorrências nos últimos 90 dias, em {r['meses']} mês(es)."],
                               ["Abrir análise de causa raiz (Ishikawa/5 porquês) e plano 5W2H."],
                               "Reincidência de NC (90 dias)"))

    # ---------------- Operacional ----------------
    t = ind.triagem_resumo()
    if t["aguardando_mais_24h"]:
        alertas.append(_alerta("alta" if t["urgentes_aguardando"] else "media", "Triagem",
                               f"{t['aguardando_mais_24h']} triagem(ns) aguardando há mais de 24 h",
                               [f"{t['aguardando']} triagens aguardando; {t['urgentes_aguardando']} urgente(s)."],
                               ["Confirmar ou cancelar as triagens paradas na fila."], "Fila de triagem"))

    if atual["pct_sem_agendamento"] and atual["pct_sem_agendamento"] >= L["sem_agendamento_pct"]:
        alertas.append(_alerta("baixa", "Dados", f"{_f0(atual['pct_sem_agendamento'])}% dos atendimentos sem agendamento correspondente",
                               ["Pode ser demanda espontânea ou falha de cadastro/identificação entre os sistemas."],
                               ["Amostrar casos e verificar se o paciente estava agendado com outro identificador."],
                               "% atendimentos sem agendamento"))

    return sorted(alertas, key=lambda a: PESO[a["severidade"]])
