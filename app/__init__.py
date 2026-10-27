"""LACEN Intelligence — fábrica da aplicação Flask."""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime

from flask import Flask, render_template, request, session
from flask_compress import Compress

from app import rotulos
from app.config import Config
from app.db import init_engine
from app.seguranca import cabecalhos_seguranca, csrf_token, pode, validar_csrf

MENU = [  # (endpoint, rótulo, ícone, recurso)
    ("painel.index", "Dashboard", "grid", "painel"),
    ("analista.index", "Analista IA", "spark", "analista"),
    ("triagem.fila", "Triagem", "clipboard", "triagem"),
    ("agendamentos.index", "Agendamentos", "calendar", "agendamentos"),
    ("laboratorio.index", "Laboratório", "flask", "laboratorio"),
    ("qualidade.index", "Qualidade", "shield", "qualidade"),
    ("processos.index", "Processos", "flow", "processos"),
    ("importacao.index", "Importação", "upload", "importacao"),
    ("configuracoes.index", "Configurações", "gear", "configuracoes"),
]
# Grupos do menu (na ordem) e a cor de cada módulo — as cores das tampas dos tubos de coleta,
# a convenção de cores que todo laboratório usa (citrato azul, EDTA lavanda, heparina verde, gel dourado…).
GRUPOS_MENU = [("Visão geral", ("painel", "analista")),
               ("Rotina do laboratório", ("triagem", "agendamentos", "laboratorio", "qualidade", "processos")),
               ("Dados", ("importacao", "configuracoes"))]
COR_MODULO = {"painel": "#3987e5", "analista": "#d55181", "triagem": "#9085e9", "agendamentos": "#199e70",
              "laboratorio": "#c98500", "qualidade": "#e66767", "processos": "#7d909f",
              "importacao": "#5d7385", "configuracoes": "#5d7385", "auth": "#3987e5"}


def _num(v, casas=0):
    if v is None:
        return "—"
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v, casas=1):
    return "—" if v is None else _num(v, casas) + "%"


def _data(v):
    if not v:
        return "—"
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return v.strftime("%d/%m/%Y %H:%M") if isinstance(v, datetime) else v.strftime("%d/%m/%Y")


def _desempenho(app: Flask) -> None:
    """Menos bytes e menos idas e voltas ao servidor a cada troca de tela.

    • Arquivos fixos (CSS, JS, fontes) ganham ?v=<impressão digital do conteúdo> e o
      navegador os guarda por 1 ano: troca de tela não pede de novo; mudou o arquivo,
      muda o ?v= e o navegador baixa a versão nova sozinho.
    • Respostas de texto (HTML, CSS, JS, JSON) vão compactadas (brotli/gzip): ~40 KB → ~8 KB.
      Compactar HTML com o token CSRF é seguro aqui porque o cookie de sessão é
      SameSite=Lax (outro site não consegue disparar requisições autenticadas em massa — BREACH).
    """
    Compress(app)
    impressoes: dict[tuple[str, float], str] = {}

    def impressao(arquivo: str) -> str | None:
        caminho = os.path.join(app.static_folder, arquivo)
        try:
            chave = (arquivo, os.path.getmtime(caminho))
        except OSError:
            return None
        if chave not in impressoes:
            with open(caminho, "rb") as f:
                impressoes[chave] = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:10]
        return impressoes[chave]

    @app.url_defaults
    def versao_estatico(endpoint, valores):
        if endpoint == "static" and "filename" in valores and "v" not in valores:
            v = impressao(valores["filename"])
            if v:
                valores["v"] = v

    @app.after_request
    def cache_estatico(resp):
        if request.endpoint == "static" and resp.status_code in (200, 304):
            if request.args.get("v"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:   # fontes chamadas de dentro do CSS (sem ?v=): mudam raríssimo
                resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp


def create_app(config: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    if app.config.get("PRODUCAO"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_engine(app.config["DATABASE_URL"])

    from app.blueprints import (agendamentos, analista, auth, configuracoes, importacao, laboratorio,
                                painel, processos, qualidade, triagem)
    for bp in (auth.bp, painel.bp, triagem.bp, agendamentos.bp, laboratorio.bp, qualidade.bp,
               processos.bp, analista.bp, importacao.bp, configuracoes.bp):
        app.register_blueprint(bp)

    app.before_request(validar_csrf)
    app.after_request(cabecalhos_seguranca)
    _desempenho(app)

    app.jinja_env.filters.update(num=_num, pct=_pct, data=_data)
    from app.services.alertas import nome_ubs
    from app.web import spark
    app.jinja_env.filters["ubs"] = nome_ubs
    app.jinja_env.globals["spark"] = spark

    @app.context_processor
    def contexto():
        menu = [m for m in MENU if pode(m[3])]
        grupos = [(nome, [m for m in menu if m[0].split(".")[0] in mods]) for nome, mods in GRUPOS_MENU]
        modulo = (request.endpoint or "").split(".")[0]
        return {"csrf_token": csrf_token, "pode": pode, "rotulo": rotulos.rotulo, "R": rotulos,
                "menu": menu, "menu_grupos": [g for g in grupos if g[1]], "cor_modulo": COR_MODULO,
                "modulo_atual": modulo, "cor_atual": COR_MODULO.get(modulo, "#3987e5"),
                "usuario_nome": session.get("usuario_nome"),
                "usuario_perfil": session.get("perfil"), "base_demo": app.config["BASE_DEMO"]}

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def erro(e):
        codigo = getattr(e, "code", 500)
        if request.path.startswith("/api/"):
            return {"erro": getattr(e, "description", "Erro interno")}, codigo
        msgs = {400: "Requisição inválida.", 401: "Faça login para continuar.",
                403: "Seu perfil não tem acesso a esta área.", 404: "Página não encontrada.",
                500: "Erro interno. O problema foi registrado."}
        if codigo == 500:
            app.logger.exception("Erro 500 em %s", request.path)
        return render_template("erro.html", codigo=codigo, mensagem=msgs.get(codigo, "Erro.")), codigo

    @app.get("/saude")
    def saude():
        from app.db import escalar
        return {"status": "ok", "banco": escalar("SELECT 1") == 1}

    return app
