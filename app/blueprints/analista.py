from flask import Blueprint, jsonify, render_template, request, session

from app.config import Config
from app.db import consultar
from app.seguranca import login_requerido
from app.services import alertas, indicadores as ind
from app.services.analista import PEDIDO_RELATORIO, PERGUNTAS_EXEMPLO, SUGESTOES, modo_ativo, perguntar

bp = Blueprint("analista", __name__, url_prefix="/analista")


def _mes_curto(d) -> str:
    return f"{ind.MESES_PT[d.month - 1][:3]}/{d:%y}"


@bp.get("/")
@login_requerido("analista")
def index():
    per = ind.periodo_padrao()          # último mês com dados: só para alertas e relatório do mês
    tot = ind.periodo_total()
    meses = [{"valor": m, "texto": f"{ind.MESES_PT[int(m[5:]) - 1]} de {m[:4]}"} for m in ind.meses_disponiveis()]
    vistos, historico = set(), []
    for h in consultar("""SELECT pergunta, max(quando) quando FROM ia_consultas WHERE usuario_id=:u
                          GROUP BY pergunta ORDER BY 2 DESC LIMIT 12""", u=session["usuario_id"]):
        q = h["pergunta"].split("\n\n(Período da análise:")[0].strip()   # o período do seletor não entra no histórico
        if q and q not in vistos:
            vistos.add(q); historico.append({"pergunta": q})
    historico = historico[:6]
    return render_template("analista/index.html", exemplos=PERGUNTAS_EXEMPLO, modo=modo_ativo(),
                           ia={k: v for k, v in Config.ia().items() if k != "chave"}, alertas=alertas.gerar_alertas(per)[:5],
                           base=ind.base_analisada(per), historico=historico, sugestoes=SUGESTOES,
                           periodo_texto=ind.rotulo_natural(per), meses=meses,
                           cobertura={"inicio": _mes_curto(tot.inicio), "fim": _mes_curto(tot.fim), "meses": len(meses),
                                      "base": ind.base_analisada(tot)},
                           ubs_lista=consultar("SELECT id, nome FROM ubs WHERE ativo ORDER BY nome"),
                           pedido_relatorio=PEDIDO_RELATORIO.format(periodo=per.rotulo()))


@bp.post("/api/perguntar")
@login_requerido("analista")
def api_perguntar():
    dados = request.get_json(silent=True) or {}
    historico = dados.get("historico") if isinstance(dados.get("historico"), list) else None
    r = perguntar(dados.get("pergunta", ""), session["usuario_id"], historico)
    return jsonify(r), (400 if "erro" in r else 200)
