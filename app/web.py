"""Utilitários compartilhados pelas telas."""
from __future__ import annotations

import json
from datetime import date, timedelta

from flask import request
from markupsafe import Markup, escape

from app.services import indicadores as ind
from app.services.indicadores import Periodo


def periodo_da_requisicao() -> Periodo:
    """?mes=AAAA-MM, ?mes=todos  ou  ?inicio=AAAA-MM-DD&fim=AAAA-MM-DD  (padrão: último mês com dados)."""
    mes = request.args.get("mes")
    if mes == "todos":
        return ind.periodo_total()
    try:
        if mes:
            ini = date.fromisoformat(mes + "-01")
            fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            return Periodo(ini, fim)
        if request.args.get("inicio") and request.args.get("fim"):
            a, b = date.fromisoformat(request.args["inicio"]), date.fromisoformat(request.args["fim"])
            return Periodo(min(a, b), max(a, b))
    except ValueError:
        pass
    return ind.periodo_padrao()


def contexto_periodo(per: Periodo) -> dict:
    return {"periodo": per, "periodo_anterior": per.anterior(), "meses": ind.meses_disponiveis(),
            "mes_atual": "todos" if per.todos else (per.inicio.strftime("%Y-%m") if per.inicio.day == 1 else None)}


def dados_json(obj) -> Markup:
    """Serializa dados para <script type="application/json"> (seguro contra injeção de HTML)."""
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return Markup(s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def spark(valores, cor: str = "#5aa7f2", largura: int = 120, altura: int = 26, classe: str = "spark",
          escala: tuple | None = None) -> Markup:
    """Minigráfico de linha em SVG (servidor, sem JavaScript). Valores None interrompem a linha;
    o último ponto ganha um marcador. Puramente decorativo: o número está sempre escrito ao lado."""
    pts = [(i, float(v)) for i, v in enumerate(valores or []) if v is not None]
    if len(pts) < 2:
        return Markup("")
    n = max(len(valores) - 1, 1)
    vmin, vmax = escala or (min(v for _, v in pts), max(v for _, v in pts))
    faixa = (vmax - vmin) or 1.0
    pad = 3
    def xy(i, v):
        return (round(pad + (largura - 2 * pad) * i / n, 1),
                round(altura - pad - (altura - 2 * pad) * (min(max(v, vmin), vmax) - vmin) / faixa, 1))
    caminho, anterior = [], None
    for i, v in enumerate(valores):
        if v is None:
            anterior = None
            continue
        x, y = xy(i, float(v))
        caminho.append(f"{'L' if anterior is not None else 'M'}{x} {y}")
        anterior = i
    ux, uy = xy(*pts[-1])
    cor, classe = escape(cor), escape(classe)
    return Markup(f'<svg class="{classe}" viewBox="0 0 {largura} {altura}" preserveAspectRatio="none" aria-hidden="true">'
                  f'<path d="{" ".join(caminho)}" fill="none" stroke="{cor}" stroke-width="1.6" '
                  f'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>'
                  f'<circle cx="{ux}" cy="{uy}" r="2.4" fill="{cor}"/></svg>')
