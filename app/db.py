"""Acesso ao PostgreSQL com SQLAlchemy Core (SQL explícito, sem ORM).

Escolha consciente: o projeto é orientado a dados e indicadores; SQL legível
é mais fácil de auditar, de explicar e de reaproveitar no Power BI.
"""
from contextlib import contextmanager
from decimal import Decimal

from flask import g, session
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def opcoes_conexao(url: str) -> dict:
    """Ajustes para o Supabase: SSL obrigatório e, no pooler, sem prepared statements."""
    if "supabase" not in url:
        return {}
    args = {"sslmode": "require"}
    if "pooler.supabase.com" in url:
        args["prepare_threshold"] = None
    return {"connect_args": args}


def criar_engine(url: str, **kw) -> Engine:
    return create_engine(url, future=True, **opcoes_conexao(url), **kw)


def init_engine(url: str) -> Engine:
    global _engine
    _engine = criar_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Banco não inicializado — chame init_engine()")
    return _engine


def _marcar_usuario(conn) -> None:
    """Informa ao trigger de auditoria quem está fazendo a alteração."""
    try:
        uid = session.get("usuario_id")
    except RuntimeError:  # fora de request (ETL por linha de comando)
        uid = getattr(g, "usuario_id", None) if g else None
    if uid:
        conn.execute(text("SELECT set_config('app.usuario_id', :u, true)"), {"u": str(uid)})


@contextmanager
def transacao(limpa_cache: bool = True):
    """Abre uma transação; commit no fim ou rollback em caso de erro.

    Depois do commit, limpa o cache dos indicadores (app/cache.py) para o painel
    refletir a alteração na hora. Escritas que não mexem em indicador
    (login, registro de pergunta à IA, fila de triagem) passam limpa_cache=False.
    """
    with engine().begin() as conn:
        _marcar_usuario(conn)
        yield conn
    if limpa_cache:
        from app import cache
        cache.limpar()


def _limpar(v):
    return float(v) if isinstance(v, Decimal) else v


def consultar(sql: str, **params) -> list[dict]:
    """Executa um SELECT e devolve lista de dicts (Decimal convertido para float, pronto para JSON)."""
    with engine().connect() as conn:
        return [{k: _limpar(v) for k, v in r._mapping.items()} for r in conn.execute(text(sql), params)]


def consultar_um(sql: str, **params) -> dict | None:
    linhas = consultar(sql, **params)
    return linhas[0] if linhas else None


def escalar(sql: str, **params):
    with engine().connect() as conn:
        return conn.execute(text(sql), params).scalar()
