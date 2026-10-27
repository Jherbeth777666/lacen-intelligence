from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.db import transacao
from app.seguranca import autenticar, bloqueado, registrar_falha

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        senha = request.form.get("senha") or ""
        chave = f"{request.remote_addr}|{email.lower()}"
        if bloqueado(chave):
            flash("Muitas tentativas. Aguarde 5 minutos e tente novamente.", "erro")
            return render_template("auth/login.html", email=email), 429
        u = autenticar(email, senha) if email and senha else None
        if not u:
            registrar_falha(chave)
            flash("E-mail ou senha inválidos.", "erro")
            return render_template("auth/login.html", email=email), 401
        session.clear()
        session.permanent = True
        session.update(usuario_id=u["id"], usuario_nome=u["nome"], perfil=u["perfil"])
        with transacao(limpa_cache=False) as c:
            c.execute(text("UPDATE usuarios SET ultimo_login = now() WHERE id = :i"), {"i": u["id"]})
        destino = request.args.get("proximo") or ""
        if not destino.startswith("/") or destino.startswith("//"):  # evita redirecionamento aberto
            destino = url_for("triagem.fila") if u["perfil"] == "triagem" else url_for("painel.index")
        return redirect(destino)
    if session.get("usuario_id"):
        return redirect(url_for("painel.index"))
    return render_template("auth/login.html")


@bp.post("/logout")
def logout():
    session.clear()
    flash("Sessão encerrada.", "ok")
    return redirect(url_for("auth.login"))
