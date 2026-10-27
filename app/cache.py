"""Cache dos indicadores em memória — evita refazer as mesmas consultas a cada troca de tela.

Os números do painel só mudam quando há importação, recálculo da camada analítica
ou alteração feita pelo próprio site (não conformidades, planos, cadastros). Por isso:

  • cada resultado fica guardado junto com uma "versão dos dados" — uma consulta
    barata (catálogo + 2 tabelas pequenas) feita no máximo uma vez por requisição;
  • importação pela linha de comando (no seu computador) muda a versão
    (nova importação ou views materializadas recalculadas) → o site recalcula sozinho;
  • escrita feita pelo site limpa o cache na hora (db.transacao);
  • mesmo sem mudança, nada fica guardado mais que CACHE_SEGUNDOS (padrão 10 min).

A fila de triagem (tempo real) NÃO passa por aqui.
"""
from __future__ import annotations

import copy
import functools
import os
import threading
import time
from collections import OrderedDict

from flask import g, has_app_context

TTL = int(os.environ.get("CACHE_SEGUNDOS", "600"))
MAX_ITENS = 400

_dados: OrderedDict = OrderedDict()
_trava = threading.Lock()
_estat = {"acertos": 0, "faltas": 0}

SQL_VERSAO = """
    SELECT concat_ws('|',
        (SELECT string_agg(relfilenode::text, ',' ORDER BY relname) FROM pg_class
          WHERE relnamespace = to_regnamespace('analitico') AND relkind = 'm'),
        (SELECT max(id) FROM importacoes),
        (SELECT count(*) || ':' || COALESCE(max(id), 0) FROM nao_conformidades),
        (SELECT count(*) || ':' || COALESCE(max(id), 0) FROM planos_acao))"""


def _versao() -> str:
    """Versão dos dados; calculada uma vez por requisição (fora de requisição: sempre)."""
    from app.db import escalar
    if has_app_context() and "_versao_dados" in g:
        return g._versao_dados
    try:
        v = escalar(SQL_VERSAO) or ""
    except Exception:   # tabela ausente em banco antigo: só o tempo limite vale
        v = ""
    if has_app_context():
        g._versao_dados = v
    return v


def limpar() -> None:
    with _trava:
        _dados.clear()
    if has_app_context():
        g.pop("_versao_dados", None)


def estatisticas() -> dict:
    return {**_estat, "itens": len(_dados), "ttl_segundos": TTL}


def em_cache(fn):
    """Decorador: guarda o resultado de fn(*args) por versão dos dados + tempo limite."""
    if TTL <= 0:
        return fn

    @functools.wraps(fn)
    def embrulho(*args, **kw):
        try:
            chave = (fn.__qualname__, args, tuple(sorted(kw.items())))
            hash(chave)
        except TypeError:          # argumento não "hasheável" (ex.: dict): não guarda
            return fn(*args, **kw)
        versao, agora = _versao(), time.monotonic()
        with _trava:
            item = _dados.get(chave)
            if item and item[0] == versao and agora - item[1] < TTL:
                _dados.move_to_end(chave)
                _estat["acertos"] += 1
                return copy.deepcopy(item[2])     # cópia: quem chama pode alterar à vontade
        _estat["faltas"] += 1
        valor = fn(*args, **kw)
        with _trava:
            _dados[chave] = (versao, agora, copy.deepcopy(valor))
            _dados.move_to_end(chave)
            while len(_dados) > MAX_ITENS:
                _dados.popitem(last=False)
        return valor

    return embrulho
