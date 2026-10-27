"""Catálogo de indicadores gerenciais.

Fonte ÚNICA de números para o dashboard, os alertas e o Analista IA — assim
o que a IA diz é exatamente o que o painel mostra. Todas as consultas leem a
camada `analitico` (sem dados pessoais).

Definições (documentadas também em docs/INDICADORES.md):
  taxa_falta          = faltas / (compareceram + faltaram)       [pendentes e agenda futura fora]
  taxa_comparecimento = compareceram / (compareceram + faltaram)
  pct_sem_agendamento = atendimentos sem agendamento correspondente nos 30 dias anteriores / atendimentos
  tat_horas           = liberação − (data do atendimento 07:00)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.cache import em_cache
from app.db import consultar, consultar_um, escalar


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date
    todos: bool = False          # True = "Todos os meses" (toda a base)

    @property
    def dias(self) -> int:
        return (self.fim - self.inicio).days + 1

    def anterior(self) -> "Periodo":
        """Período imediatamente anterior. Se for um mês cheio, compara com o mês cheio anterior."""
        if self.inicio.day == 1 and (self.fim + timedelta(days=1)).day == 1:
            fim = self.inicio - timedelta(days=1)
            return Periodo(fim.replace(day=1), fim)
        return Periodo(self.inicio - timedelta(days=self.dias), self.inicio - timedelta(days=1))

    def rotulo(self) -> str:
        if self.todos:
            return f"Todos os meses ({self.inicio:%d/%m/%Y} a {self.fim:%d/%m/%Y})"
        return f"{self.inicio:%d/%m/%Y} a {self.fim:%d/%m/%Y}"

    def p(self) -> dict:
        return {"ini": self.inicio, "fim": self.fim}


def _pct(a, b):
    return round(100.0 * float(a) / float(b), 1) if b else None


# ---------------------------------------------------------------------------
# Cobertura da base
# ---------------------------------------------------------------------------
@em_cache
def cobertura() -> dict:
    return consultar_um("""
        SELECT (SELECT min(data) FROM agendamentos WHERE status IN ('COMPARECEU','FALTOU')) AS agend_ini,
               (SELECT max(data) FROM agendamentos WHERE status IN ('COMPARECEU','FALTOU')) AS agend_fim,
               (SELECT min(data) FROM atendimentos) AS atend_ini,
               (SELECT max(data) FROM atendimentos) AS atend_fim,
               (SELECT count(*) FROM agendamentos) AS agendamentos,
               (SELECT count(*) FROM atendimentos) AS atendimentos,
               (SELECT count(*) FROM atendimento_exames) AS exames,
               (SELECT count(*) FROM nao_conformidades) AS nao_conformidades,
               (SELECT count(*) FROM ubs WHERE ativo) AS ubs""")


def periodo_padrao() -> Periodo:
    """Último mês com agenda realizada (é o que move a maioria dos indicadores).

    Sem agenda, usa o último mês com produção. Assim, alguns atendimentos do Worklab que já
    caem no mês seguinte não fazem o painel abrir num mês quase vazio.
    """
    c = cobertura()
    ultimo = c["agend_fim"] or c["atend_fim"] or date.today()
    return Periodo(ultimo.replace(day=1), ultimo)


def periodo_total() -> Periodo:
    """Toda a base: do primeiro ao último dia com dados realizados (agendamento ou produção)."""
    c = cobertura()
    inicios = [d for d in (c["agend_ini"], c["atend_ini"]) if d]
    fins = [d for d in (c["agend_fim"], c["atend_fim"]) if d]
    if not inicios:
        return periodo_padrao()
    return Periodo(min(inicios), max(fins), todos=True)


@em_cache
def meses_disponiveis() -> list[str]:
    return [r["m"] for r in consultar("""
        SELECT DISTINCT to_char(data,'YYYY-MM') m FROM (
            SELECT data FROM agendamentos WHERE status IN ('COMPARECEU','FALTOU')
            UNION ALL SELECT data FROM atendimentos) x ORDER BY 1 DESC""")]


@em_cache
def base_analisada(per: Periodo) -> dict:
    """Metadados que acompanham qualquer análise (exigência: mostrar a origem)."""
    r = consultar_um("""
        SELECT (SELECT count(*) FROM agendamentos WHERE data BETWEEN :ini AND :fim) AS agendamentos,
               (SELECT count(*) FROM atendimentos WHERE data BETWEEN :ini AND :fim) AS atendimentos,
               (SELECT count(*) FROM analitico.fato_exames WHERE data BETWEEN :ini AND :fim) AS exames,
               (SELECT count(*) FROM nao_conformidades WHERE data BETWEEN :ini AND :fim) AS nao_conformidades,
               (SELECT count(DISTINCT ubs_id) FROM agendamentos WHERE data BETWEEN :ini AND :fim) AS ubs""",
                     **per.p())
    total = r["agendamentos"] + r["atendimentos"] + r["exames"] + r["nao_conformidades"]
    return {"periodo": per.rotulo(), "inicio": per.inicio.isoformat(), "fim": per.fim.isoformat(),
            "ubs_analisadas": r["ubs"], "registros": total, "detalhe": r}


# ---------------------------------------------------------------------------
# KPIs executivos
# ---------------------------------------------------------------------------
@em_cache
def kpis(per: Periodo) -> dict:
    ag = consultar_um("""
        SELECT count(*) agendamentos, sum(realizado) realizados, sum(compareceu) compareceram, sum(faltou) faltas,
               count(*) FILTER (WHERE status='PENDENTE') pendentes
        FROM analitico.fato_agendamentos WHERE data BETWEEN :ini AND :fim""", **per.p())
    at = consultar_um("""
        SELECT count(*) atendimentos, count(DISTINCT paciente_pseudo) pacientes,
               COALESCE(sum(qtd_exames),0) exames, COALESCE(sum(valor_total),0) valor,
               count(*) FILTER (WHERE NOT tinha_agendamento) sem_agendamento,
               count(DISTINCT data) dias
        FROM analitico.fato_atendimentos WHERE data BETWEEN :ini AND :fim""", **per.p())
    nc = consultar_um("""
        SELECT count(*) FILTER (WHERE data BETWEEN :ini AND :fim) no_periodo,
               count(*) FILTER (WHERE status IN ('ABERTA','EM_TRATAMENTO')) abertas,
               count(*) FILTER (WHERE vencida) vencidas
        FROM analitico.fato_nao_conformidades""", **per.p())
    realizados = ag["realizados"] or 0
    return {
        "pacientes": at["pacientes"], "atendimentos": at["atendimentos"], "exames": int(at["exames"]),
        "valor": float(at["valor"]), "agendamentos": ag["agendamentos"], "realizados": realizados,
        "compareceram": ag["compareceram"] or 0, "faltas": ag["faltas"] or 0, "pendentes": ag["pendentes"] or 0,
        "taxa_comparecimento": _pct(ag["compareceram"] or 0, realizados),
        "taxa_falta": _pct(ag["faltas"] or 0, realizados),
        "media_exames_atendimento": round(float(at["exames"]) / at["atendimentos"], 2) if at["atendimentos"] else None,
        "media_atendimentos_dia": round(at["atendimentos"] / at["dias"], 1) if at["dias"] else None,
        "pct_sem_agendamento": _pct(at["sem_agendamento"], at["atendimentos"]),
        "nc_periodo": nc["no_periodo"], "nc_abertas": nc["abertas"], "nc_vencidas": nc["vencidas"],
    }


@em_cache
def kpis_comparados(per: Periodo) -> dict:
    atual, ant = kpis(per), kpis(per.anterior())
    var = {}
    for k, v in atual.items():
        a = ant.get(k)
        if isinstance(v, (int, float)) and isinstance(a, (int, float)):
            if k.startswith("taxa") or k.startswith("pct"):
                var[k] = {"tipo": "pp", "valor": round(v - a, 1)}
            elif a:
                var[k] = {"tipo": "%", "valor": round(100.0 * (v - a) / a, 1)}
    return {"atual": atual, "anterior": ant, "variacao": var, "periodo_anterior": per.anterior().rotulo()}


# ---------------------------------------------------------------------------
# Séries temporais
# ---------------------------------------------------------------------------
@em_cache
def serie_diaria(per: Periodo, ubs_id: int | None = None) -> list[dict]:
    return consultar("""
        WITH d AS (SELECT data FROM analitico.dim_calendario WHERE data BETWEEN :ini AND :fim AND dia_util),
        ag AS (SELECT data, count(*) agendados, sum(realizado) realizados, sum(compareceu) compareceram, sum(faltou) faltas
               FROM analitico.fato_agendamentos WHERE data BETWEEN :ini AND :fim
                 AND (CAST(:ubs AS int) IS NULL OR ubs_id = :ubs) GROUP BY 1),
        at AS (SELECT data, count(*) atendimentos, sum(qtd_exames) exames
               FROM analitico.fato_atendimentos WHERE data BETWEEN :ini AND :fim
                 AND (CAST(:ubs AS int) IS NULL OR ubs_id = :ubs) GROUP BY 1)
        SELECT d.data, COALESCE(agendados,0) agendados, COALESCE(faltas,0) faltas,
               round(100.0*faltas/NULLIF(realizados,0),1) taxa_falta,
               round(100.0*compareceram/NULLIF(realizados,0),1) taxa_comparecimento,
               COALESCE(atendimentos,0) atendimentos, COALESCE(exames,0)::int exames
        FROM d LEFT JOIN ag USING (data) LEFT JOIN at USING (data) ORDER BY d.data""",
                     ubs=ubs_id, **per.p())


@em_cache
def evolucao_mensal(ubs_id: int | None = None) -> list[dict]:
    return consultar("""
        WITH ag AS (SELECT to_char(data,'YYYY-MM') m, count(*) agendados, sum(realizado) realizados,
                           sum(compareceu) compareceram, sum(faltou) faltas FROM analitico.fato_agendamentos
                    WHERE status <> 'AGENDADO' AND (CAST(:ubs AS int) IS NULL OR ubs_id=:ubs) GROUP BY 1),
             at AS (SELECT to_char(data,'YYYY-MM') m, count(*) atendimentos, count(DISTINCT paciente_pseudo) pacientes,
                           sum(qtd_exames) exames FROM analitico.fato_atendimentos
                    WHERE (CAST(:ubs AS int) IS NULL OR ubs_id=:ubs) GROUP BY 1),
             nc AS (SELECT to_char(data,'YYYY-MM') m, count(*) ncs FROM nao_conformidades
                    WHERE (CAST(:ubs AS int) IS NULL OR ubs_id=:ubs) GROUP BY 1)
        SELECT COALESCE(ag.m, at.m) mes, COALESCE(agendados,0) agendados, COALESCE(faltas,0) faltas,
               COALESCE(compareceram,0) compareceram,
               round(100.0*faltas/NULLIF(realizados,0),1) taxa_falta,
               round(100.0*compareceram/NULLIF(realizados,0),1) taxa_comparecimento,
               COALESCE(atendimentos,0) atendimentos, COALESCE(pacientes,0) pacientes,
               COALESCE(exames,0)::int exames, COALESCE(ncs,0) nao_conformidades
        FROM ag FULL JOIN at ON at.m = ag.m LEFT JOIN nc ON nc.m = COALESCE(ag.m, at.m)
        ORDER BY 1""", ubs=ubs_id)


# ---------------------------------------------------------------------------
# UBS
# ---------------------------------------------------------------------------
@em_cache
def ranking_ubs(per: Periodo) -> list[dict]:
    ant = per.anterior()
    linhas = consultar("""
        WITH ag AS (SELECT ubs_id, count(*) agendados, sum(realizado) realizados, sum(compareceu) compareceram,
                           sum(faltou) faltas
                    FROM analitico.fato_agendamentos WHERE data BETWEEN :ini AND :fim GROUP BY 1),
             ag0 AS (SELECT ubs_id, sum(realizado) realizados, sum(faltou) faltas
                    FROM analitico.fato_agendamentos WHERE data BETWEEN :ini0 AND :fim0 GROUP BY 1),
             at AS (SELECT ubs_id, count(*) atendimentos, sum(qtd_exames) exames
                    FROM analitico.fato_atendimentos WHERE data BETWEEN :ini AND :fim GROUP BY 1),
             rec AS (SELECT ubs_id, count(*) FILTER (WHERE situacao='RECOLETA') recoletas, count(*) exames_total
                    FROM analitico.fato_exames WHERE data BETWEEN :ini AND :fim GROUP BY 1)
        SELECT u.id ubs_id, u.nome ubs, COALESCE(ag.agendados,0) agendados, COALESCE(ag.realizados,0) realizados,
               COALESCE(ag.compareceram,0) compareceram, COALESCE(ag.faltas,0) faltas,
               round(100.0*ag.faltas/NULLIF(ag.realizados,0),1) taxa_falta,
               round(100.0*ag.compareceram/NULLIF(ag.realizados,0),1) taxa_comparecimento,
               round(100.0*ag0.faltas/NULLIF(ag0.realizados,0),1) taxa_falta_anterior,
               COALESCE(ag0.faltas,0) faltas_anterior, COALESCE(ag0.realizados,0) realizados_anterior,
               COALESCE(at.atendimentos,0) atendimentos, COALESCE(at.exames,0)::int exames,
               round(100.0*rec.recoletas/NULLIF(rec.exames_total,0),2) taxa_recoleta,
               round(100.0*ag.agendados/NULLIF(sum(ag.agendados) OVER (),0),1) participacao_demanda
        FROM ubs u LEFT JOIN ag ON ag.ubs_id=u.id LEFT JOIN ag0 ON ag0.ubs_id=u.id
             LEFT JOIN at ON at.ubs_id=u.id LEFT JOIN rec ON rec.ubs_id=u.id
        WHERE u.ativo AND (ag.agendados IS NOT NULL OR at.atendimentos IS NOT NULL)
        ORDER BY taxa_falta DESC NULLS LAST""", ini0=ant.inicio, fim0=ant.fim, **per.p())
    for l in linhas:
        a, b = l["taxa_falta"], l["taxa_falta_anterior"]
        l["variacao_pp"] = round(float(a) - float(b), 1) if a is not None and b is not None else None
        for k in ("taxa_falta", "taxa_comparecimento", "taxa_falta_anterior", "taxa_recoleta", "participacao_demanda"):
            l[k] = float(l[k]) if l[k] is not None else None
    return linhas


@em_cache
def faltas_dia_semana(per: Periodo, ubs_id: int | None = None) -> list[dict]:
    return consultar("""
        SELECT c.num_dia_semana, c.dia_semana, sum(f.realizado) realizados, sum(f.faltou) faltas,
               round(100.0*sum(f.faltou)/NULLIF(sum(f.realizado),0),1) taxa_falta
        FROM analitico.fato_agendamentos f JOIN analitico.dim_calendario c ON c.data = f.data
        WHERE f.data BETWEEN :ini AND :fim AND (CAST(:ubs AS int) IS NULL OR f.ubs_id=:ubs)
        GROUP BY 1,2 ORDER BY 1""", ubs=ubs_id, **per.p())


@em_cache
def concentracao_horario(per: Periodo) -> list[dict]:
    return consultar("""
        SELECT COALESCE(to_char(horario,'HH24:MI'),'sem horário') horario, count(*) agendamentos,
               round(100.0*count(*)/sum(count(*)) OVER (),1) pct
        FROM agendamentos WHERE data BETWEEN :ini AND :fim GROUP BY 1 ORDER BY 2 DESC""", **per.p())


@em_cache
def cruzamento_agendamento_producao(per: Periodo) -> dict:
    r = consultar_um("""
        SELECT sum(compareceu) compareceram,
               count(*) FILTER (WHERE compareceu=1 AND atendido_no_lab) compareceu_e_atendido,
               count(*) FILTER (WHERE faltou=1 AND atendido_no_lab) faltou_mas_atendido
        FROM analitico.fato_agendamentos WHERE data BETWEEN :ini AND :fim""", **per.p())
    s = consultar_um("""SELECT count(*) atendimentos, count(*) FILTER (WHERE NOT tinha_agendamento) sem_agendamento,
                               count(*) FILTER (WHERE origem_ubs='NAO_IDENTIFICADA') sem_ubs
                        FROM analitico.fato_atendimentos WHERE data BETWEEN :ini AND :fim""", **per.p())
    return {**r, **s,
            "pct_comparecidos_com_atendimento": _pct(r["compareceu_e_atendido"] or 0, r["compareceram"] or 0),
            "pct_atendimentos_sem_agendamento": _pct(s["sem_agendamento"], s["atendimentos"])}


# ---------------------------------------------------------------------------
# Laboratório
# ---------------------------------------------------------------------------
@em_cache
def top_exames(per: Periodo, n: int = 10) -> list[dict]:
    ant = per.anterior()
    linhas = consultar("""
        WITH a AS (SELECT sigla, exame, setor, sum(quantidade) qtd FROM analitico.fato_exames
                   WHERE data BETWEEN :ini AND :fim GROUP BY 1,2,3),
             b AS (SELECT sigla, sum(quantidade) qtd FROM analitico.fato_exames
                   WHERE data BETWEEN :ini0 AND :fim0 GROUP BY 1)
        SELECT a.sigla, a.exame, a.setor, a.qtd::int quantidade, b.qtd::int quantidade_anterior,
               round(100.0*a.qtd/sum(a.qtd) OVER (),1) participacao
        FROM a LEFT JOIN b USING (sigla) ORDER BY a.qtd DESC LIMIT :n""",
                       ini0=ant.inicio, fim0=ant.fim, n=n, **per.p())
    for l in linhas:
        b = l["quantidade_anterior"]
        l["variacao_pct"] = round(100.0 * (l["quantidade"] - b) / b, 1) if b else None
        l["participacao"] = float(l["participacao"])
    return linhas


@em_cache
def variacao_exames(per: Periodo, minimo: int = 30) -> list[dict]:
    """Exames com maior crescimento/queda relativa (só os com volume relevante)."""
    linhas = [l for l in top_exames(per, 200) if l["quantidade_anterior"] and l["quantidade_anterior"] >= minimo]
    return sorted(linhas, key=lambda l: l["variacao_pct"], reverse=True)


@em_cache
def producao_setor(per: Periodo) -> list[dict]:
    linhas = consultar("""
        SELECT setor, sum(quantidade)::int exames, round(sum(valor),2) valor,
               round(avg(tat_horas),1) tat_medio_h,
               round(percentile_cont(0.9) WITHIN GROUP (ORDER BY tat_horas)::numeric,1) tat_p90_h,
               round(100.0*avg(dentro_prazo::int),1) pct_no_prazo,
               count(*) FILTER (WHERE situacao='RECOLETA') recoletas,
               count(*) FILTER (WHERE situacao='EM_ANALISE') em_analise
        FROM analitico.fato_exames WHERE data BETWEEN :ini AND :fim GROUP BY 1 ORDER BY 2 DESC""", **per.p())
    for l in linhas:
        for k in ("valor", "tat_medio_h", "tat_p90_h", "pct_no_prazo"):
            l[k] = float(l[k]) if l[k] is not None else None
    return linhas


@em_cache
def tat_setor_mensal() -> list[dict]:
    return consultar("""
        SELECT setor, to_char(data,'YYYY-MM') mes, round(avg(tat_horas),1)::float tat_medio_h,
               round(100.0*avg(dentro_prazo::int),1)::float pct_no_prazo, count(*) exames
        FROM analitico.fato_exames WHERE tat_horas IS NOT NULL GROUP BY 1,2 ORDER BY 1,2""")


@em_cache
def exames_por_ubs(per: Periodo) -> list[dict]:
    return consultar("""
        SELECT u.nome ubs, count(DISTINCT f.atendimento_id) atendimentos, sum(f.quantidade)::int exames,
               round(sum(f.quantidade)::numeric/NULLIF(count(DISTINCT f.atendimento_id),0),2)::float exames_por_atendimento
        FROM analitico.fato_exames f JOIN ubs u ON u.id=f.ubs_id
        WHERE f.data BETWEEN :ini AND :fim GROUP BY 1 ORDER BY 3 DESC""", **per.p())


# ---------------------------------------------------------------------------
# Qualidade
# ---------------------------------------------------------------------------
@em_cache
def nc_resumo(per: Periodo) -> dict:
    ant = per.anterior()
    geral = consultar_um("""
        SELECT count(*) FILTER (WHERE data BETWEEN :ini AND :fim) no_periodo,
               count(*) FILTER (WHERE data BETWEEN :ini0 AND :fim0) periodo_anterior,
               count(*) FILTER (WHERE status IN ('ABERTA','EM_TRATAMENTO')) abertas,
               count(*) FILTER (WHERE vencida) vencidas,
               round(avg(dias_resolucao) FILTER (WHERE concluida_em BETWEEN :ini AND :fim),1)::float tempo_medio_resolucao_dias,
               count(*) FILTER (WHERE status='CONCLUIDA' AND concluida_em BETWEEN :ini AND :fim) concluidas_no_periodo
        FROM analitico.fato_nao_conformidades""", ini0=ant.inicio, fim0=ant.fim, **per.p())
    por_categoria = consultar("""
        SELECT categoria, etapa, count(*) qtd,
               count(*) FILTER (WHERE status IN ('ABERTA','EM_TRATAMENTO')) abertas
        FROM analitico.fato_nao_conformidades WHERE data BETWEEN :ini AND :fim
        GROUP BY 1,2 ORDER BY 3 DESC""", **per.p())
    reincidentes = consultar("""
        SELECT categoria, count(*) ocorrencias_90d, count(DISTINCT to_char(data,'YYYY-MM')) meses
        FROM analitico.fato_nao_conformidades WHERE data BETWEEN CAST(:fim AS date) - 89 AND :fim
        GROUP BY 1 HAVING count(*) >= 3 ORDER BY 2 DESC""", fim=per.fim)
    por_setor = consultar("""
        SELECT COALESCE(setor,'Sem setor') setor, count(*) qtd FROM analitico.fato_nao_conformidades
        WHERE data BETWEEN :ini AND :fim GROUP BY 1 ORDER BY 2 DESC""", **per.p())
    planos = consultar_um("""
        SELECT count(*) total, count(*) FILTER (WHERE status='CONCLUIDA') concluidas,
               count(*) FILTER (WHERE status IN ('PLANEJADA','EM_ANDAMENTO') AND quando < CURRENT_DATE) atrasadas,
               count(*) FILTER (WHERE status IN ('PLANEJADA','EM_ANDAMENTO')) abertas
        FROM planos_acao""")
    return {"geral": geral, "por_categoria": por_categoria, "reincidentes": reincidentes,
            "por_setor": por_setor, "planos": planos}


# ---------------------------------------------------------------------------
# Triagem (operacional, tempo real)
# ---------------------------------------------------------------------------
def triagem_resumo() -> dict:
    return consultar_um("""
        SELECT count(*) FILTER (WHERE situacao='AGUARDANDO') aguardando,
               count(*) FILTER (WHERE situacao='AGUARDANDO' AND horas_em_aberto > 24) aguardando_mais_24h,
               count(*) FILTER (WHERE data = CURRENT_DATE) hoje,
               count(*) FILTER (WHERE data = CURRENT_DATE AND situacao IN ('CONFIRMADA','COLETADA')) confirmadas_hoje,
               count(*) FILTER (WHERE prioridade='URGENTE' AND situacao='AGUARDANDO') urgentes_aguardando,
               round(avg(horas_em_aberto * 60) FILTER (WHERE situacao IN ('CONFIRMADA','COLETADA')
                     AND data >= CURRENT_DATE - 7),0)::int minutos_medios_ate_confirmar
        FROM analitico.fato_triagens""")


@em_cache
def total_ubs() -> int:
    return escalar("SELECT count(*) FROM ubs WHERE ativo")


# ---------------------------------------------------------------------------
# Painéis: metas, destaques, mapas e perfis
# ---------------------------------------------------------------------------
# Metas gerenciais (referência inicial — ajuste à pactuação do laboratório).
# sentido "max": quanto menor melhor (o valor deve ficar ATÉ a meta); "min": deve ficar ACIMA da meta.
METAS = [
    {"chave": "taxa_falta", "nome": "Taxa de faltas", "detalhe": "faltas ÷ (compareceram + faltaram)",
     "sentido": "max", "meta": 20.0, "unidade": "%"},
    {"chave": "pct_no_prazo", "nome": "Exames liberados no prazo", "detalhe": "prazo do catálogo de exames",
     "sentido": "min", "meta": 90.0, "unidade": "%"},
    {"chave": "taxa_recoleta", "nome": "Taxa de recoleta", "detalhe": "exames com recoleta ÷ exames",
     "sentido": "max", "meta": 2.0, "unidade": "%"},
    {"chave": "pct_sem_agendamento", "nome": "Atendimentos sem agendamento", "detalhe": "sem agendamento nos 30 dias anteriores",
     "sentido": "max", "meta": 10.0, "unidade": "%"},
    {"chave": "nc_vencidas", "nome": "Não conformidades vencidas", "detalhe": "abertas com prazo vencido (hoje)",
     "sentido": "max", "meta": 0, "unidade": ""},
    {"chave": "triagem_24h", "nome": "Triagens paradas há mais de 24 h", "detalhe": "fila em tempo real",
     "sentido": "max", "meta": 0, "unidade": ""},
]


def _status_meta(valor, meta, sentido) -> str:
    if valor is None:
        return "sem"
    if sentido == "max":
        if valor <= meta:
            return "ok"
        return "warn" if valor <= (meta * 1.25 if meta else 2) else "ruim"
    if valor >= meta:
        return "ok"
    return "warn" if valor >= meta * 0.9 else "ruim"


def metas(per: Periodo, atual: dict | None = None) -> list[dict]:
    """Cada meta com valor atual, situação (ok/warn/ruim/sem) e geometria da barra (0–100%)."""
    atual = atual or kpis(per)
    lab = consultar_um("""
        SELECT round(100.0*avg(dentro_prazo::int),1)::float pct_no_prazo,
               round(100.0*count(*) FILTER (WHERE situacao='RECOLETA')/NULLIF(count(*),0),2)::float taxa_recoleta
        FROM analitico.fato_exames WHERE data BETWEEN :ini AND :fim""", **per.p())
    valores = {**atual, **lab, "triagem_24h": (triagem_resumo() or {}).get("aguardando_mais_24h")}
    saida = []
    for m in METAS:
        v = valores.get(m["chave"])
        v = float(v) if v is not None else None
        escala = 100.0 if m["unidade"] == "%" and m["sentido"] == "min" else max(m["meta"] * 2, (v or 0) * 1.15, 4)
        saida.append({**m, "valor": v, "status": _status_meta(v, m["meta"], m["sentido"]),
                      "preench": min(100.0, 100.0 * (v or 0) / escala), "alvo": min(100.0, 100.0 * m["meta"] / escala)})
    return saida


def _br(v, casas: int = 1) -> str:
    """Número no formato brasileiro: 1.234,5"""
    return f"{v:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def destaques(per: Periodo, k: dict, ranking: list[dict]) -> list[dict]:
    """Frases curtas, calculadas (sem IA), sobre o que mais mudou no período.
    Cada item: seta (sobe/desce/fato), tom (bom/ruim/neutro), sujeito (em destaque) e texto."""
    itens = []
    if not k["atual"]["atendimentos"] and not k["atual"]["realizados"]:
        return []   # período sem dados: nada a destacar (evita "caíram 100%")
    if not per.todos:
        var, ant, at = k["variacao"], k["anterior"], k["atual"]
        if "atendimentos" in var and ant.get("atendimentos") and at.get("atendimentos"):
            v = var["atendimentos"]["valor"]
            itens.append({"seta": "sobe" if v > 0 else "desce", "tom": "neutro", "sujeito": "Atendimentos",
                          "texto": f"{'subiram' if v > 0 else 'caíram'} {_br(abs(v))}% "
                                   f"({_br(ant['atendimentos'], 0)} → {_br(at['atendimentos'], 0)})"})
        if "taxa_falta" in var and var["taxa_falta"]["valor"]:
            v = var["taxa_falta"]["valor"]
            itens.append({"seta": "sobe" if v > 0 else "desce", "tom": "ruim" if v > 0 else "bom",
                          "sujeito": "Taxa de faltas da rede",
                          "texto": f"{'subiu' if v > 0 else 'caiu'} {_br(abs(v))} p.p., para {_br(at['taxa_falta'])}%"})
        comparaveis = [r for r in ranking if r["variacao_pp"] is not None and (r["realizados"] or 0) >= 30
                       and (r["realizados_anterior"] or 0) >= 30]
        if comparaveis:
            pior = max(comparaveis, key=lambda r: r["variacao_pp"])
            melhor = min(comparaveis, key=lambda r: r["variacao_pp"])
            if pior["variacao_pp"] > 0:
                itens.append({"seta": "sobe", "tom": "ruim", "sujeito": pior["ubs"],
                              "texto": f"teve o maior aumento de faltas: +{_br(pior['variacao_pp'])} p.p. "
                                       f"(agora {_br(pior['taxa_falta'])}%)"})
            if melhor["variacao_pp"] < 0:
                itens.append({"seta": "desce", "tom": "bom", "sujeito": melhor["ubs"],
                              "texto": f"teve a maior redução de faltas: {_br(melhor['variacao_pp'])} p.p. "
                                       f"(agora {_br(melhor['taxa_falta'])}%)"})
        var_ex = variacao_exames(per, minimo=100)
        if var_ex and var_ex[0]["variacao_pct"] > 0:
            e = var_ex[0]
            itens.append({"seta": "sobe", "tom": "neutro", "sujeito": e["exame"],
                          "texto": f"foi o exame que mais cresceu: +{_br(e['variacao_pct'])}% "
                                   f"({_br(e['quantidade_anterior'], 0)} → {_br(e['quantidade'], 0)})"})
    else:
        com_dados = [r for r in ranking if (r["realizados"] or 0) >= 30 and r["taxa_falta"] is not None]
        if com_dados:
            r = max(com_dados, key=lambda r: r["taxa_falta"])
            itens.append({"seta": "fato", "tom": "ruim", "sujeito": r["ubs"],
                          "texto": f"tem a maior taxa de faltas da base: {_br(r['taxa_falta'])}% "
                                   f"em {_br(r['realizados'], 0)} agendamentos realizados"})
        top = top_exames(per, 1)
        if top:
            itens.append({"seta": "fato", "tom": "neutro", "sujeito": top[0]["exame"],
                          "texto": f"é o exame mais realizado: {_br(top[0]['quantidade'], 0)} "
                                   f"({_br(top[0]['participacao'])}% do total)"})
    setores = [s for s in producao_setor(per) if s["pct_no_prazo"] is not None]
    if setores:
        s = min(setores, key=lambda s: s["pct_no_prazo"])
        if s["pct_no_prazo"] < 90:
            itens.append({"seta": "fato", "tom": "ruim", "sujeito": s["setor"],
                          "texto": f"liberou só {_br(s['pct_no_prazo'])}% dos exames no prazo "
                                   f"(tempo médio de {_br(s['tat_medio_h'], 0)} h)"})
    from app.services.alertas import nome_ubs  # import tardio: alertas depende deste módulo
    nomes_ubs = {r["ubs"] for r in ranking}
    for i in itens:
        if i["sujeito"] in nomes_ubs:
            i["sujeito"] = nome_ubs(i["sujeito"])
    return itens[:6]


def serie_mensal_ultimos(chave: str, n: int = 8, ubs_id: int | None = None) -> list:
    """Últimos n meses de uma métrica de evolucao_mensal (para minigráficos)."""
    return [m[chave] for m in evolucao_mensal(ubs_id)[-n:]]


@em_cache
def tendencia_ubs(n_meses: int = 6, ate: date | None = None) -> dict[int, list]:
    """Taxa de faltas mensal de cada UBS nos n meses com dados até `ate` (None = sem agendamento realizado)."""
    linhas = consultar("""
        SELECT ubs_id, to_char(data,'YYYY-MM') m, sum(realizado) realizados, sum(faltou) faltas
        FROM analitico.fato_agendamentos WHERE realizado = 1 GROUP BY 1,2""")
    limite = ate.strftime("%Y-%m") if ate else "9999-12"
    meses = sorted({l["m"] for l in linhas if l["m"] <= limite})[-n_meses:]
    tab: dict[int, dict] = {}
    for l in linhas:
        if l["m"] in meses and l["realizados"]:
            tab.setdefault(l["ubs_id"], {})[l["m"]] = round(100.0 * l["faltas"] / l["realizados"], 1)
    return {u: [v.get(m) for m in meses] for u, v in tab.items()}


def _mistura(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


@em_cache
def mapa_calor_ubs_dia(per: Periodo, minimo: int = 30) -> dict:
    """Taxa de faltas por UBS × dia da semana. Rampa sequencial de um só tom (vermelho),
    do escuro (pouca falta) ao claro (muita falta); o número fica sempre escrito na célula."""
    linhas = consultar("""
        SELECT u.id ubs_id, u.nome ubs, c.num_dia_semana d, sum(f.realizado) n, sum(f.faltou) faltas
        FROM analitico.fato_agendamentos f JOIN analitico.dim_calendario c ON c.data = f.data
             JOIN ubs u ON u.id = f.ubs_id
        WHERE f.data BETWEEN :ini AND :fim AND f.realizado = 1 GROUP BY 1,2,3""", **per.p())
    dias = sorted({l["d"] for l in linhas if l["d"] <= 6}) or [1, 2, 3, 4, 5]
    nomes = {1: "Seg", 2: "Ter", 3: "Qua", 4: "Qui", 5: "Sex", 6: "Sáb"}
    por_ubs: dict[int, dict] = {}
    for l in linhas:
        u = por_ubs.setdefault(l["ubs_id"], {"ubs": l["ubs"], "dias": {}, "n": 0, "faltas": 0})
        u["dias"][l["d"]] = l
        u["n"] += l["n"]
        u["faltas"] += l["faltas"]
    topo = 60.0
    def celula(n, f):
        if not n or n < 10:
            return {"txt": "—" if not n else f"n={n}", "bg": None, "cor": None, "titulo": f"{n or 0} agendamentos realizados (amostra pequena)"}
        taxa = 100.0 * f / n
        t = min(1.0, taxa / topo)
        bg = _mistura("#1b2a35", "#e66767", t)
        cor = "#141a1f" if t > 0.62 else "#e8eef2"
        return {"txt": f"{taxa:.0f}%", "bg": bg, "cor": cor, "titulo": f"{_br(taxa)}% de faltas em {_br(n, 0)} agendamentos realizados"}
    linhas_out = []
    for u in sorted(por_ubs.values(), key=lambda u: -(u["faltas"] / u["n"] if u["n"] else 0)):
        if u["n"] < minimo:
            continue
        linhas_out.append({"ubs": u["ubs"], "total": celula(u["n"], u["faltas"]),
                           "celulas": [celula(u["dias"].get(d, {}).get("n"), u["dias"].get(d, {}).get("faltas", 0)) for d in dias]})
    return {"dias": [nomes.get(d, str(d)) for d in dias], "linhas": linhas_out, "topo": topo}


@em_cache
def perfil_atendimento(per: Periodo) -> list[dict]:
    """Quantos exames cada atendimento pediu (1, 2, … 12+)."""
    linhas = consultar("""
        SELECT LEAST(qtd_exames, 12) exames, count(*) atendimentos
        FROM analitico.fato_atendimentos WHERE data BETWEEN :ini AND :fim AND qtd_exames > 0
        GROUP BY 1 ORDER BY 1""", **per.p())
    for l in linhas:
        l["rotulo"] = "12+" if l["exames"] >= 12 else str(l["exames"])
    return linhas


@em_cache
def nc_pareto(per: Periodo) -> list[dict]:
    """Categorias de NC em ordem decrescente com percentual acumulado (análise de Pareto)."""
    linhas = consultar("""
        SELECT categoria, count(*) qtd FROM analitico.fato_nao_conformidades
        WHERE data BETWEEN :ini AND :fim GROUP BY 1 ORDER BY 2 DESC, 1""", **per.p())
    total = sum(l["qtd"] for l in linhas) or 1
    acum = 0
    for l in linhas:
        acum += l["qtd"]
        l["pct_acumulado"] = round(100.0 * acum / total, 1)
    return linhas


@em_cache
def nc_por_etapa(per: Periodo) -> list[dict]:
    return consultar("""
        SELECT etapa, count(*) qtd,
               count(*) FILTER (WHERE status IN ('ABERTA','EM_TRATAMENTO')) abertas,
               count(*) FILTER (WHERE vencida) vencidas,
               round(avg(dias_resolucao),1)::float dias_resolucao
        FROM analitico.fato_nao_conformidades WHERE data BETWEEN :ini AND :fim
        GROUP BY 1 ORDER BY 2 DESC""", **per.p())


def triagem_historico(dias: int = 30) -> dict:
    """Volume diário de triagens e tempo até a confirmação por prioridade (sem dados pessoais)."""
    diario = consultar("""
        SELECT c.data, COALESCE(t.total,0) total, COALESCE(t.urgentes,0) urgentes
        FROM analitico.dim_calendario c
        LEFT JOIN (SELECT data, count(*) total, count(*) FILTER (WHERE prioridade <> 'ROTINA') urgentes
                   FROM analitico.fato_triagens GROUP BY 1) t ON t.data = c.data
        WHERE c.data BETWEEN CURRENT_DATE - CAST(:d AS int) + 1 AND CURRENT_DATE ORDER BY 1""", d=dias)
    por_prioridade = consultar("""
        SELECT prioridade, count(*) total,
               count(*) FILTER (WHERE situacao='AGUARDANDO') aguardando,
               round(avg(horas_em_aberto*60) FILTER (WHERE situacao IN ('CONFIRMADA','COLETADA')),0)::int minutos_ate_confirmar
        FROM analitico.fato_triagens WHERE data >= CURRENT_DATE - CAST(:d AS int) + 1
        GROUP BY 1 ORDER BY CASE prioridade WHEN 'URGENTE' THEN 0 WHEN 'PRIORITARIA' THEN 1 ELSE 2 END""", d=dias)
    return {"diario": diario, "por_prioridade": por_prioridade}


# ---------------------------------------------------------------------------
# Comparecimento: jornada do paciente, faltas repetidas, matriz de prioridade
# ---------------------------------------------------------------------------
@em_cache
def jornada_paciente(per: Periodo, ubs_id: int | None = None) -> list[dict]:
    """Do agendamento ao atendimento no laboratório: cada etapa com % sobre o total agendado.

    Atendido no laboratório = compareceu e tem atendimento no Worklab em até 7 dias.
    """
    r = consultar_um("""
        SELECT count(*) agendados, COALESCE(sum(realizado),0) realizados,
               COALESCE(sum(compareceu),0) compareceram,
               count(*) FILTER (WHERE compareceu = 1 AND atendido_no_lab) atendidos
        FROM analitico.fato_agendamentos
        WHERE data BETWEEN :ini AND :fim AND (CAST(:ubs AS int) IS NULL OR ubs_id = :ubs)""",
                     ubs=ubs_id, **per.p())
    if not r or not r["agendados"]:
        return []
    etapas = [("agendados", "Agendados", "Todos os agendamentos do período"),
              ("realizados", "Com registro", "Comparecimento ou falta registrados"),
              ("compareceram", "Compareceram", "Compareceram à coleta"),
              ("atendidos", "Atendidos no laboratório", "Com atendimento no Worklab em até 7 dias")]
    total, anterior, saida = r["agendados"], None, []
    for chave, rotulo, ajuda in etapas:
        v = r[chave] or 0
        saida.append({"etapa": rotulo, "ajuda": ajuda, "quantidade": v, "pct_total": _pct(v, total),
                      "pct_etapa_anterior": _pct(v, anterior) if anterior is not None else None})
        anterior = v
    return saida


@em_cache
def faltas_repetidas(per: Periodo, ubs_id: int | None = None) -> dict:
    """Quantos pacientes faltaram 1, 2 ou 3+ vezes no período e quanto das faltas eles somam.

    Só contagens agregadas: o pseudônimo do paciente é usado para agrupar e nunca sai do banco.
    """
    linhas = consultar("""
        WITH p AS (SELECT paciente_pseudo, sum(faltou) f FROM analitico.fato_agendamentos
                   WHERE data BETWEEN :ini AND :fim AND paciente_pseudo IS NOT NULL
                     AND (CAST(:ubs AS int) IS NULL OR ubs_id = :ubs)
                   GROUP BY 1 HAVING sum(faltou) > 0)
        SELECT LEAST(f, 3)::int vezes, count(*) pacientes, sum(f)::int faltas FROM p GROUP BY 1 ORDER BY 1""",
                       ubs=ubs_id, **per.p())
    rotulos = {1: "1 falta", 2: "2 faltas", 3: "3 ou mais"}
    faixas = {l["vezes"]: l for l in linhas}
    tot_f = sum(l["faltas"] for l in linhas)
    tot_p = sum(l["pacientes"] for l in linhas)
    rep = [l for l in linhas if l["vezes"] >= 2]
    return {
        "faixas": [{"faixa": rotulos[v], "pacientes": faixas.get(v, {}).get("pacientes", 0),
                    "faltas": faixas.get(v, {}).get("faltas", 0)} for v in (1, 2, 3)],
        "pacientes_com_falta": tot_p, "faltas": tot_f,
        "pacientes_repetidos": sum(l["pacientes"] for l in rep),
        "pct_faltas_de_repetidos": _pct(sum(l["faltas"] for l in rep), tot_f),
    }


# ---------------------------------------------------------------------------
# Dashboard: UBS em destaque e números do período
# ---------------------------------------------------------------------------
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def destaques_ubs(per: Periodo, minimo: int = 30, n: int = 5) -> dict:
    """UBS com maior comparecimento e as que mais reduziram faltas (vs. período anterior), com amostra mínima."""
    rk = [r for r in ranking_ubs(per) if (r["realizados"] or 0) >= minimo and r["taxa_comparecimento"] is not None]
    melhores = sorted(rk, key=lambda r: (-r["taxa_comparecimento"], -r["realizados"]))[:n]
    melhoraram = sorted([r for r in rk if r["variacao_pp"] is not None and r["variacao_pp"] < 0
                         and (r["realizados_anterior"] or 0) >= minimo], key=lambda r: r["variacao_pp"])[:n]
    return {"comparecimento": melhores, "melhoraram": melhoraram, "minimo": minimo}


def numeros_periodo(per: Periodo) -> list[dict]:
    """Curiosidades do período, todas calculadas dos dados (cada uma some se não houver dado)."""
    out = []
    dias = [d for d in serie_diaria(per) if d["agendados"]]
    if dias:
        d = max(dias, key=lambda x: x["agendados"])
        out.append({"rotulo": "Dia mais movimentado", "icone": "calendar",
                    "valor": f"{d['data']:%d/%m}", "detalhe": f"{DIAS_SEMANA[d['data'].weekday()]} · "
                    f"{d['agendados']} agendamentos"})
    hs = [h for h in concentracao_horario(per) if h["horario"] != "sem horário"]
    rep = faltas_repetidas(per)
    if hs and float(hs[0]["pct"]) < 90:
        out.append({"rotulo": "Horário mais disputado", "icone": "clock", "valor": hs[0]["horario"],
                    "detalhe": f"{_br(float(hs[0]['pct']))}% dos agendamentos marcados"})
    elif rep["faltas"]:     # horário único (ex.: todos às 07:00) não é novidade: mostra quem falta de novo
        out.append({"rotulo": "Faltas de quem já faltou", "icone": "trend", "valor": f"{_br(rep['pct_faltas_de_repetidos'] or 0)}%",
                    "detalhe": f"{_br(rep['pacientes_repetidos'], 0)} pacientes faltaram 2+ vezes no período"})
    ex = top_exames(per, 1)
    if ex:
        out.append({"rotulo": "Exame mais realizado", "icone": "flask", "valor": ex[0]["sigla"],
                    "detalhe": f"{ex[0]['exame']} · {_br(ex[0]['quantidade'], 0)} exames ({_br(ex[0]['participacao'])}%)"})
    sem = [s for s in faltas_dia_semana(per) if (s["realizados"] or 0) >= 30 and s["taxa_falta"] is not None]
    if sem:
        s = max(sem, key=lambda x: x["taxa_falta"])
        out.append({"rotulo": "Dia com mais faltas", "icone": "alert", "valor": s["dia_semana"],
                    "detalhe": f"{_br(float(s['taxa_falta']))}% de faltas nesse dia da semana"})
    k = kpis(per)
    if k["media_exames_atendimento"]:
        out.append({"rotulo": "Exames por atendimento", "icone": "layers", "valor": _br(k["media_exames_atendimento"]),
                    "detalhe": f"média em {_br(k['atendimentos'], 0)} atendimentos no laboratório"})
    rk = [r for r in ranking_ubs(per) if r["agendados"]]
    if rk:
        r = max(rk, key=lambda x: x["agendados"])
        out.append({"rotulo": "UBS com mais agendamentos", "icone": "pin", "valor": r["ubs"], "ubs": True,
                    "detalhe": f"{_br(r['agendados'], 0)} agendamentos · {_br(r['participacao_demanda'])}% da demanda"})
    return out


def horario_unico(per: Periodo, limiar: float = 90.0) -> dict | None:
    """Se quase todos os agendamentos têm o mesmo horário (ex.: todos às 07:00), devolve o resumo; senão None.

    Os poucos horários diferentes costumam ser erro de digitação na planilha de origem.
    """
    hs = [h for h in concentracao_horario(per) if h["horario"] != "sem horário"]
    if not hs or float(hs[0]["pct"]) < limiar:
        return None
    total = sum(h["agendamentos"] for h in hs)
    return {"horario": hs[0]["horario"], "pct": float(hs[0]["pct"]), "agendamentos": hs[0]["agendamentos"],
            "total": total, "outros": [{"horario": h["horario"], "n": h["agendamentos"]} for h in hs[1:]],
            "n_outros": total - hs[0]["agendamentos"]}


MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
            "outubro", "novembro", "dezembro"]


def rotulo_natural(per: Periodo) -> str:
    """'setembro de 2026' (mês único), 'todo o período' ou 'de 03/09 a 18/09/2026'."""
    if per.todos:
        return "todo o período com dados"
    if per.inicio.year == per.fim.year and per.inicio.month == per.fim.month and per.inicio.day == 1:
        return f"{MESES_PT[per.inicio.month - 1]} de {per.inicio.year}"
    return f"de {per.inicio:%d/%m} a {per.fim:%d/%m/%Y}"


@em_cache
def exame_detalhe(termo: str, per: Periodo) -> dict | None:
    """Um exame específico (sigla ou parte do nome): série mensal, setor e distribuição por UBS no período."""
    from etl.normalizacao import normalizar_texto
    alvo = normalizar_texto(termo)
    if not alvo:
        return None
    cands = consultar("SELECT DISTINCT exame_id, sigla, exame, setor FROM analitico.fato_exames")
    exato = [c for c in cands if normalizar_texto(c["sigla"]) == alvo]
    parcial = [c for c in cands if alvo in normalizar_texto(c["exame"]) or alvo in normalizar_texto(c["sigla"])]
    achados = exato or parcial
    if not achados:
        return None
    e = min(achados, key=lambda c: len(c["exame"]))
    mensal = consultar("""SELECT to_char(data,'YYYY-MM') mes, sum(quantidade)::int quantidade
                          FROM analitico.fato_exames WHERE exame_id = :e GROUP BY 1 ORDER BY 1""", e=e["exame_id"])
    por_ubs = consultar("""SELECT COALESCE(u.nome,'Não identificada') ubs, sum(f.quantidade)::int quantidade
                           FROM analitico.fato_exames f LEFT JOIN ubs u ON u.id = f.ubs_id
                           WHERE f.exame_id = :e AND f.data BETWEEN :ini AND :fim
                           GROUP BY 1 ORDER BY 2 DESC LIMIT 15""", e=e["exame_id"], **per.p())
    total = escalar("SELECT COALESCE(sum(quantidade),0) FROM analitico.fato_exames WHERE data BETWEEN :ini AND :fim",
                    **per.p()) or 0
    no_periodo = sum(u["quantidade"] for u in por_ubs) if por_ubs else 0
    return {"sigla": e["sigla"], "exame": e["exame"], "setor": e["setor"], "mensal": mensal, "por_ubs": por_ubs,
            "quantidade_periodo": no_periodo, "pct_do_total": _pct(no_periodo, total),
            "outros_parecidos": [c["exame"] for c in achados if c["exame_id"] != e["exame_id"]][:5]}


@em_cache
def kpis_agenda(per: Periodo, ubs_id: int | None = None) -> dict:
    """Indicadores da agenda (rede ou uma UBS): agendados, compareceram, faltas, taxas e pendentes."""
    r = consultar_um("""
        SELECT count(*) agendamentos, COALESCE(sum(realizado),0) realizados, COALESCE(sum(compareceu),0) compareceram,
               COALESCE(sum(faltou),0) faltas, count(*) FILTER (WHERE status='PENDENTE') pendentes
        FROM analitico.fato_agendamentos
        WHERE data BETWEEN :ini AND :fim AND (CAST(:ubs AS int) IS NULL OR ubs_id = :ubs)""", ubs=ubs_id, **per.p())
    return {**r, "taxa_comparecimento": _pct(r["compareceram"], r["realizados"]),
            "taxa_falta": _pct(r["faltas"], r["realizados"])}


def kpis_agenda_comparados(per: Periodo, ubs_id: int | None = None) -> dict:
    atual, ant = kpis_agenda(per, ubs_id), kpis_agenda(per.anterior(), ubs_id)
    var = {}
    for k, v in atual.items():
        a = ant.get(k)
        if isinstance(v, (int, float)) and isinstance(a, (int, float)):
            if k.startswith("taxa"):
                var[k] = {"tipo": "pp", "valor": round(v - a, 1)}
            elif a:
                var[k] = {"tipo": "%", "valor": round(100.0 * (v - a) / a, 1)}
    return {"atual": atual, "anterior": ant, "variacao": var, "periodo_anterior": per.anterior().rotulo()}
