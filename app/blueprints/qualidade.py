"""Gestão de não conformidades (NC) com planos de ação 5W2H."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app import rotulos
from app.db import consultar, consultar_um, transacao
from app.seguranca import login_requerido
from app.services import indicadores as ind
from app.web import contexto_periodo, dados_json, periodo_da_requisicao

bp = Blueprint("qualidade", __name__, url_prefix="/qualidade")


@bp.get("/")
@login_requerido("qualidade")
def index():
    per = periodo_da_requisicao()
    status = request.args.get("status", "ABERTAS")
    etapa = request.args.get("etapa", "")
    cond = {"ABERTAS": "n.status IN ('ABERTA','EM_TRATAMENTO')", "TODAS": "TRUE",
            "VENCIDAS": "n.status IN ('ABERTA','EM_TRATAMENTO') AND n.prazo < CURRENT_DATE"}.get(status, "n.status = :st")
    lista = consultar(f"""
        SELECT n.id, n.data, n.etapa, n.categoria, n.prioridade, n.status, n.prazo, s.nome setor, u.nome ubs,
               (n.status IN ('ABERTA','EM_TRATAMENTO') AND n.prazo < CURRENT_DATE) vencida,
               (SELECT count(*) FROM planos_acao p WHERE p.nc_id=n.id) planos
        FROM nao_conformidades n LEFT JOIN setores s ON s.id=n.setor_id LEFT JOIN ubs u ON u.id=n.ubs_id
        WHERE {cond} AND (:etapa = '' OR n.etapa = :etapa)
        ORDER BY vencida DESC, CASE n.prioridade WHEN 'CRITICA' THEN 0 WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
                 n.data DESC LIMIT 300""", st=status, etapa=etapa)
    resumo = ind.nc_resumo(per)
    mensal = consultar("""SELECT to_char(data,'YYYY-MM') mes, count(*) total,
                                 count(*) FILTER (WHERE etapa='PRE_ANALITICA') pre,
                                 count(*) FILTER (WHERE etapa='ANALITICA') analitica,
                                 count(*) FILTER (WHERE etapa='POS_ANALITICA') pos,
                                 count(*) FILTER (WHERE etapa='ADMINISTRATIVA') adm
                          FROM nao_conformidades GROUP BY 1 ORDER BY 1""")
    return render_template("qualidade/index.html", lista=lista, resumo=resumo, status=status, etapa=etapa,
                           por_etapa=ind.nc_por_etapa(per),
                           graficos=dados_json({"mensal": mensal, "categorias": resumo["por_categoria"],
                                                "pareto": ind.nc_pareto(per)}),
                           **contexto_periodo(per))


def _opcoes():
    return {"setores": consultar("SELECT id, nome FROM setores ORDER BY nome"),
            "ubs": consultar("SELECT id, nome FROM ubs WHERE ativo ORDER BY nome"),
            "usuarios": consultar("SELECT id, nome FROM usuarios WHERE ativo ORDER BY nome"),
            "categorias": rotulos.CATEGORIAS_NC}


CAMPOS = ("data", "setor_id", "ubs_id", "etapa", "categoria", "problema", "causa", "responsavel_id", "prioridade",
          "prazo", "status", "acao_corretiva", "observacao", "concluida_em")


def _ler_form(f) -> tuple[dict, list[str]]:
    d = {k: (f.get(k) or "").strip() or None for k in CAMPOS}
    erros = []
    for k in ("setor_id", "ubs_id", "responsavel_id"):
        d[k] = int(d[k]) if d[k] and d[k].isdigit() else None
    for k in ("data", "prazo", "concluida_em"):
        try:
            d[k] = date.fromisoformat(d[k]) if d[k] else None
        except ValueError:
            erros.append(f"Data inválida em '{k}'.")
            d[k] = None
    d["data"] = d["data"] or date.today()
    if d["etapa"] not in rotulos.ETAPA:
        erros.append("Selecione a etapa do processo.")
    if not d["categoria"]:
        erros.append("Selecione a categoria.")
    if not d["problema"] or len(d["problema"]) < 10:
        erros.append("Descreva o problema (mínimo 10 caracteres).")
    d["prioridade"] = d["prioridade"] if d["prioridade"] in rotulos.PRIORIDADE_NC else "MEDIA"
    d["status"] = d["status"] if d["status"] in rotulos.STATUS_NC else "ABERTA"
    if d["status"] == "CONCLUIDA":
        d["concluida_em"] = d["concluida_em"] or date.today()
        if not d["acao_corretiva"]:
            erros.append("Para concluir, registre a ação corretiva aplicada.")
    else:
        d["concluida_em"] = None
    if d["concluida_em"] and d["concluida_em"] < d["data"]:
        erros.append("A conclusão não pode ser anterior à data da NC.")
    if d["prazo"] and d["prazo"] < d["data"]:
        erros.append("O prazo não pode ser anterior à data da NC.")
    return d, erros


@bp.route("/nova", methods=["GET", "POST"])
@login_requerido("qualidade_editar")
def nova():
    if request.method == "POST":
        d, erros = _ler_form(request.form)
        if erros:
            for e in erros:
                flash(e, "erro")
            return render_template("qualidade/form.html", nc=d, novo=True, **_opcoes()), 400
        with transacao() as c:
            nid = c.execute(text(f"""INSERT INTO nao_conformidades ({', '.join(CAMPOS)}, criado_por)
                                     VALUES ({', '.join(':' + k for k in CAMPOS)}, :cp) RETURNING id"""),
                            {**d, "cp": session["usuario_id"]}).scalar()
        flash(f"Não conformidade #{nid} registrada.", "ok")
        return redirect(url_for("qualidade.detalhe", nid=nid))
    return render_template("qualidade/form.html", nc={"data": date.today(), "prioridade": "MEDIA", "status": "ABERTA"},
                           novo=True, **_opcoes())


@bp.route("/<int:nid>", methods=["GET", "POST"])
@login_requerido("qualidade")
def detalhe(nid):
    nc = consultar_um("""SELECT n.*, s.nome setor, u.nome ubs, r.nome responsavel, c.nome criador
                         FROM nao_conformidades n LEFT JOIN setores s ON s.id=n.setor_id LEFT JOIN ubs u ON u.id=n.ubs_id
                         LEFT JOIN usuarios r ON r.id=n.responsavel_id LEFT JOIN usuarios c ON c.id=n.criado_por
                         WHERE n.id=:i""", i=nid)
    if not nc:
        abort(404)
    if request.method == "POST":
        from app.seguranca import pode
        if not pode("qualidade_editar"):
            abort(403)
        d, erros = _ler_form(request.form)
        if erros:
            for e in erros:
                flash(e, "erro")
            return render_template("qualidade/form.html", nc={**nc, **d}, novo=False, **_opcoes()), 400
        with transacao() as c:
            c.execute(text(f"UPDATE nao_conformidades SET {', '.join(f'{k}=:{k}' for k in CAMPOS)} WHERE id=:id"),
                      {**d, "id": nid})
        flash("Não conformidade atualizada.", "ok")
        return redirect(url_for("qualidade.detalhe", nid=nid))
    planos = consultar("SELECT * FROM planos_acao WHERE nc_id=:i ORDER BY id", i=nid)
    editar = request.args.get("editar") == "1"
    if editar:
        return render_template("qualidade/form.html", nc=nc, novo=False, **_opcoes())
    return render_template("qualidade/detalhe.html", nc=nc, planos=planos, hoje=date.today())


@bp.post("/<int:nid>/plano")
@login_requerido("qualidade_editar")
def novo_plano(nid):
    f = {k: (request.form.get(k) or "").strip() for k in ("o_que", "por_que", "onde", "quando", "quem", "como", "quanto")}
    if not f["o_que"] or not f["por_que"]:
        flash("Preencha pelo menos 'O quê' e 'Por quê'.", "erro")
        return redirect(url_for("qualidade.detalhe", nid=nid))
    try:
        f["quando"] = date.fromisoformat(f["quando"]) if f["quando"] else None
    except ValueError:
        f["quando"] = None
    with transacao() as c:
        c.execute(text("""INSERT INTO planos_acao (nc_id, o_que, por_que, onde, quando, quem, como, quanto)
                          VALUES (:nc, :o_que, :por_que, :onde, :quando, :quem, :como, :quanto)"""), {"nc": nid, **f})
        c.execute(text("UPDATE nao_conformidades SET status='EM_TRATAMENTO' WHERE id=:i AND status='ABERTA'"), {"i": nid})
    flash("Plano de ação 5W2H adicionado.", "ok")
    return redirect(url_for("qualidade.detalhe", nid=nid))


@bp.post("/plano/<int:pid>/status")
@login_requerido("qualidade_editar")
def status_plano(pid):
    st = request.form.get("status")
    if st not in rotulos.STATUS_PLANO:
        abort(400)
    with transacao() as c:
        nid = c.execute(text("UPDATE planos_acao SET status=:s WHERE id=:i RETURNING nc_id"), {"s": st, "i": pid}).scalar()
    if not nid:
        abort(404)
    return redirect(url_for("qualidade.detalhe", nid=nid))
