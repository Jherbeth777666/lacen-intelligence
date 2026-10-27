from flask import Blueprint, render_template, request

from app.db import consultar
from app.seguranca import login_requerido
from app.services import indicadores as ind
from app.services.alertas import nome_ubs
from app.web import contexto_periodo, dados_json, periodo_da_requisicao

bp = Blueprint("agendamentos", __name__, url_prefix="/agendamentos")


@bp.get("/")
@login_requerido("agendamentos")
def index():
    per = periodo_da_requisicao()
    ubs_id = request.args.get("ubs", type=int)
    ranking = ind.ranking_ubs(per)
    k = ind.kpis_comparados(per)
    repetidas = ind.faltas_repetidas(per, ubs_id)
    # matriz de prioridade: só UBS com amostra suficiente; nomes curtos para os rótulos
    matriz = [{"ubs_id": r["ubs_id"], "ubs": nome_ubs(r["ubs"]), "realizados": r["realizados"], "faltas": r["faltas"],
               "taxa_falta": r["taxa_falta"]} for r in ranking if (r["realizados"] or 0) >= 30 and r["taxa_falta"] is not None]
    graficos = {"dia_semana": ind.faltas_dia_semana(per, ubs_id), "diario": ind.serie_diaria(per, ubs_id),
                "mensal": ind.evolucao_mensal(ubs_id), "horario": ind.concentracao_horario(per),
                "meta_falta": next(m["meta"] for m in ind.METAS if m["chave"] == "taxa_falta"),
                "matriz": matriz, "taxa_rede": k["atual"]["taxa_falta"], "ubs_id": ubs_id,
                "repetidas": repetidas["faixas"]}
    futuros = consultar("""SELECT u.nome ubs, count(*) qtd, min(a.data) primeira, max(a.data) ultima
                           FROM agendamentos a JOIN ubs u ON u.id=a.ubs_id
                           WHERE a.status='AGENDADO' AND a.data > CURRENT_DATE GROUP BY 1 ORDER BY 2 DESC LIMIT 8""")
    ubs_nome = next((nome_ubs(r["ubs"]) for r in ranking if r["ubs_id"] == ubs_id), None) if ubs_id else None
    if ubs_id and not ubs_nome:
        u = consultar("SELECT nome FROM ubs WHERE id = :i", i=ubs_id)
        ubs_nome = nome_ubs(u[0]["nome"]) if u else None
    topo = ind.kpis_agenda_comparados(per, ubs_id) if ubs_id else ind.kpis_agenda_comparados(per)
    return render_template("agendamentos/index.html", k=k, ranking=ranking, n_matriz=len(matriz),
                           topo=topo, ubs_nome=ubs_nome, rede=ind.kpis_agenda(per),
                           jornada=ind.jornada_paciente(per, ubs_id), repetidas=repetidas,
                           horario_unico=ind.horario_unico(per),
                           tem_recoleta=any(r["taxa_recoleta"] for r in ranking), meta_falta=graficos["meta_falta"],
                           cruzamento=ind.cruzamento_agendamento_producao(per), futuros=futuros,
                           mapa=ind.mapa_calor_ubs_dia(per), tendencia=ind.tendencia_ubs(ate=per.fim),
                           ubs_lista=consultar("SELECT id, nome FROM ubs WHERE ativo ORDER BY nome"), ubs_id=ubs_id,
                           graficos=dados_json(graficos), **contexto_periodo(per))
