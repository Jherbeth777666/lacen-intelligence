from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import rotulos
from app.config import Config
from app.db import consultar, transacao
from app.seguranca import hash_senha, login_requerido, senha_forte
from app.services.alertas import LIMIARES
from app.services.indicadores import METAS
from etl.normalizacao import normalizar_texto

bp = Blueprint("configuracoes", __name__, url_prefix="/configuracoes")


@bp.get("/")
@login_requerido("configuracoes")
def index():
    ubs = consultar("""SELECT u.id, u.nome, u.tipo, u.ativo,
                              (SELECT string_agg(alias, ' · ' ORDER BY alias) FROM ubs_alias a WHERE a.ubs_id=u.id) aliases,
                              (SELECT count(*) FROM agendamentos g WHERE g.ubs_id=u.id) agendamentos
                       FROM ubs u ORDER BY u.nome""")
    usuarios = consultar("SELECT id, nome, email, perfil, ativo, ultimo_login FROM usuarios ORDER BY nome")
    exames = consultar("""SELECT e.sigla, e.nome, COALESCE(s.nome,'—') setor, e.valor_ref, e.prazo_horas
                          FROM exames e LEFT JOIN setores s ON s.id=e.setor_id ORDER BY s.nome, e.sigla""")
    return render_template("configuracoes/index.html", ubs=ubs, usuarios=usuarios, exames=exames,
                           limiares=LIMIARES, metas=METAS,
                           ia={"curto": {"gemini": "Gemini", "anthropic": "Claude"}.get(Config.ia()["provedor"], "Offline"),
                               "modelo": Config.ia()["modelo"]},
                           pseudo_ok=bool(Config.PSEUDO_SECRET))


@bp.post("/ubs/alias")
@login_requerido("configuracoes")
def novo_alias():
    alias, ubs_id = normalizar_texto(request.form.get("alias")), request.form.get("ubs_id", type=int)
    if not alias or not ubs_id:
        flash("Informe o nome alternativo e a UBS.", "erro")
    else:
        with transacao() as c:
            c.execute(text("INSERT INTO ubs_alias (alias, ubs_id) VALUES (:a,:u) ON CONFLICT (alias) DO UPDATE SET ubs_id=:u"),
                      {"a": alias, "u": ubs_id})
        flash(f"'{alias}' passa a ser reconhecido na importação.", "ok")
    return redirect(url_for("configuracoes.index") + "#ubs")


@bp.post("/ubs/nova")
@login_requerido("configuracoes")
def nova_ubs():
    nome = " ".join((request.form.get("nome") or "").upper().split())
    tipo = request.form.get("tipo", "UBS")
    if len(nome) < 4:
        flash("Nome da UBS muito curto.", "erro")
    else:
        try:
            with transacao() as c:
                c.execute(text("INSERT INTO ubs (nome, tipo) VALUES (:n, :t)"), {"n": nome, "t": tipo})
            flash(f"UBS '{nome}' cadastrada.", "ok")
        except IntegrityError:
            flash("UBS já cadastrada ou tipo inválido.", "erro")
    return redirect(url_for("configuracoes.index") + "#ubs")


@bp.post("/usuarios/novo")
@login_requerido("configuracoes")
def novo_usuario():
    nome, email = (request.form.get("nome") or "").strip(), (request.form.get("email") or "").strip().lower()
    perfil, senha = request.form.get("perfil"), request.form.get("senha") or ""
    erro = senha_forte(senha)
    if not nome or "@" not in email or perfil not in rotulos.PERFIL:
        erro = "Preencha nome, e-mail e perfil."
    if erro:
        flash(erro, "erro")
        return redirect(url_for("configuracoes.index") + "#usuarios")
    try:
        with transacao() as c:
            c.execute(text("INSERT INTO usuarios (nome, email, senha_hash, perfil) VALUES (:n,:e,:h,:p)"),
                      {"n": nome, "e": email, "h": hash_senha(senha), "p": perfil})
        flash(f"Usuário {email} criado.", "ok")
    except IntegrityError:
        flash("Já existe um usuário com esse e-mail.", "erro")
    return redirect(url_for("configuracoes.index") + "#usuarios")


@bp.post("/usuarios/<int:uid>/ativo")
@login_requerido("configuracoes")
def alternar_usuario(uid):
    from flask import session
    if uid == session["usuario_id"]:
        flash("Você não pode desativar o próprio usuário.", "erro")
    else:
        with transacao() as c:
            c.execute(text("UPDATE usuarios SET ativo = NOT ativo WHERE id=:i"), {"i": uid})
    return redirect(url_for("configuracoes.index") + "#usuarios")
