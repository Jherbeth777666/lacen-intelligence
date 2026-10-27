from flask import Blueprint, render_template

from app.seguranca import login_requerido
from app.services import alertas, indicadores as ind
from app.web import contexto_periodo, dados_json, periodo_da_requisicao

bp = Blueprint("painel", __name__)


@bp.get("/")
@login_requerido("painel")
def index():
    per = periodo_da_requisicao()
    k = ind.kpis_comparados(per)
    ranking = ind.ranking_ubs(per)
    mensal = ind.evolucao_mensal()
    ult = [m for m in mensal if m["mes"] <= per.fim.strftime("%Y-%m")][-8:]   # minigráficos terminam no período escolhido
    series = {c: [m[c] for m in ult] for c in ("pacientes", "exames", "agendados", "taxa_falta", "nao_conformidades")}
    series["comparecimento"] = [round(100 - m["taxa_falta"], 1) if m["taxa_falta"] is not None else None for m in ult]
    meta_falta = next(m["meta"] for m in ind.METAS if m["chave"] == "taxa_falta")
    graficos = {
        "diario": ind.serie_diaria(per),
        "mensal": mensal,
        "ranking": [r for r in ranking if (r["realizados"] or 0) >= 30],
        "top_exames": ind.top_exames(per, 500),
        "taxa_rede": k["atual"]["taxa_falta"],
        "meta_falta": meta_falta,
    }
    return render_template("dashboard/index.html", k=k, alertas=alertas.gerar_alertas(per),
                           mes_texto=ind.rotulo_natural(per), mes_anterior_texto=ind.rotulo_natural(per.anterior()).replace(f" de {per.inicio.year}", ""),
                           destaque_ubs=ind.destaques_ubs(per), numeros=ind.numeros_periodo(per), meta_falta=meta_falta,
                           destaques=ind.destaques(per, k, ranking),
                           series=series, n_ranking=len(graficos["ranking"]), n_exames=len(graficos["top_exames"]), base=ind.base_analisada(per), triagem=ind.triagem_resumo(),
                           graficos=dados_json(graficos), **contexto_periodo(per))
