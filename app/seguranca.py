"""Autenticação, autorização por perfil, CSRF, limite de tentativas e mascaramento."""
from __future__ import annotations

import secrets
import time
from functools import wraps

import bcrypt
from flask import current_app, abort, flash, g, redirect, request, session, url_for

from app.db import consultar_um

# --------------------------------------------------------------------------- permissões
# Quem pode acessar cada módulo. Dados pessoais (nome/CPF) só aparecem em Triagem.
PERMISSOES = {
    "painel": {"admin", "gestor", "triagem", "qualidade"},
    "triagem": {"admin", "triagem", "gestor"},
    "triagem_editar": {"admin", "triagem"},
    "agendamentos": {"admin", "gestor"},
    "laboratorio": {"admin", "gestor", "qualidade"},
    "qualidade": {"admin", "gestor", "qualidade"},
    "qualidade_editar": {"admin", "qualidade", "gestor"},
    "processos": {"admin", "gestor", "qualidade", "triagem"},
    "analista": {"admin", "gestor", "qualidade"},
    "importacao": {"admin", "gestor"},
    "configuracoes": {"admin"},
}


def pode(recurso: str) -> bool:
    return session.get("perfil") in PERMISSOES.get(recurso, set())


def login_requerido(recurso: str | None = None):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if not session.get("usuario_id"):
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("auth.login", proximo=request.path))
            if recurso and not pode(recurso):
                abort(403)
            g.usuario_id = session["usuario_id"]
            return fn(*a, **kw)
        return wrap
    return deco


# --------------------------------------------------------------------------- senha / login
def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def senha_forte(senha: str) -> str | None:
    if len(senha) < 10:
        return "A senha precisa ter pelo menos 10 caracteres."
    if senha.lower() == senha or senha.upper() == senha or not any(c.isdigit() for c in senha):
        return "Use letras maiúsculas, minúsculas e números."
    return None


_TENTATIVAS: dict[str, list[float]] = {}
MAX_TENTATIVAS, JANELA_S = 5, 300


def bloqueado(chave: str) -> bool:
    agora = time.time()
    _TENTATIVAS[chave] = [t for t in _TENTATIVAS.get(chave, []) if agora - t < JANELA_S]
    return len(_TENTATIVAS[chave]) >= MAX_TENTATIVAS


def registrar_falha(chave: str) -> None:
    _TENTATIVAS.setdefault(chave, []).append(time.time())


_HASH_FALSO = bcrypt.hashpw(b"senha-inexistente", bcrypt.gensalt()).decode()


def autenticar(email: str, senha: str) -> dict | None:
    u = consultar_um("SELECT id, nome, email, senha_hash, perfil, ativo FROM usuarios WHERE lower(email) = lower(:e)",
                     e=email.strip())
    # compara mesmo quando o usuário não existe, para não revelar e-mails válidos pelo tempo de resposta
    alvo = u["senha_hash"] if u else _HASH_FALSO
    try:
        ok = bcrypt.checkpw(senha.encode(), alvo.encode())
    except ValueError:
        ok = False
    return u if (u and ok and u["ativo"]) else None


# --------------------------------------------------------------------------- CSRF
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def validar_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    enviado = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not enviado or not secrets.compare_digest(enviado, session.get("_csrf", "")):
        if request.path.startswith("/api/"):
            abort(400, "Token CSRF inválido")
        flash("Sua sessão expirou. Tente novamente.", "erro")
        abort(400)


# --------------------------------------------------------------------------- privacidade
def mascarar_cpf(cpf: str | None) -> str:
    d = "".join(c for c in (cpf or "") if c.isdigit())
    return f"***.{d[3:6]}.{d[6:9]}-**" if len(d) == 11 else "—"


def mascarar_cns(cns: str | None) -> str:
    d = "".join(c for c in (cns or "") if c.isdigit())
    return f"*** **** **** {d[-4:]}" if len(d) == 15 else "—"


CSP = ("default-src 'self'; "
       "script-src 'self'; "
       "style-src 'self' 'unsafe-inline'; "
       "font-src 'self' data:; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def cabecalhos_seguranca(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if current_app.config.get("PRODUCAO"):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"   # sistema interno: fora do Google
    if request.path.startswith(("/triagem", "/api/")):
        resp.headers["Cache-Control"] = "no-store"  # páginas com dado pessoal não ficam em cache
    return resp
