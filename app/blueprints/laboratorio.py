from flask import Blueprint, render_template

from app.seguranca import login_requerido
from app.services import indicadores as ind
from app.web import contexto_periodo, dados_json, periodo_da_requisicao

bp = Blueprint("laboratorio", __name__, url_prefix="/laboratorio")


@bp.get("/")
@login_requerido("laboratorio")
def index():
    per = periodo_da_requisicao()
    setores = ind.producao_setor(per)
    variacao = ind.variacao_exames(per)
    # maiores altas e quedas (só exames com volume relevante no período anterior)
    altas = [v for v in variacao if v["variacao_pct"] > 0][:6]
    quedas = [v for v in variacao if v["variacao_pct"] < 0][-6:]
    graficos = {"tat_mensal": ind.tat_setor_mensal(), "diario": ind.serie_diaria(per),
                "setores": setores, "variacao": altas + quedas, "perfil": ind.perfil_atendimento(per)}
    por_ubs = ind.exames_por_ubs(per)
    # colunas de prazo/recoleta só aparecem se a base trouxer esses dados (planilhas sem liberação não têm)
    tem_tat = any(x["tat_medio_h"] is not None for x in setores) or any(t["tat_medio_h"] is not None for t in graficos["tat_mensal"])
    tem_situacao = any(x["recoletas"] or x["em_analise"] for x in setores)
    dias = [d for d in graficos["diario"] if d["exames"]]
    pico = max(dias, key=lambda d: d["exames"]) if dias else None
    return render_template("laboratorio/index.html", k=ind.kpis_comparados(per), setores=setores,
                           tem_tat=tem_tat, tem_situacao=tem_situacao, pico=pico,
                           total_exames=sum(x["exames"] for x in setores) or 1,
                           top=ind.top_exames(per, 500), variacao=variacao, por_ubs=por_ubs,
                           max_ubs=max([u["exames"] for u in por_ubs] or [1]),
                           graficos=dados_json(graficos), **contexto_periodo(per))
