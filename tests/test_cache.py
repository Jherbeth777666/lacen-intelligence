"""Cache dos indicadores: reaproveita resultados e percebe quando os dados mudam."""
from datetime import date

from sqlalchemy import text

from app import cache
from app.db import transacao
from app.services import indicadores as ind
from app.services.indicadores import Periodo

AGO = Periodo(date(2026, 8, 1), date(2026, 8, 31))


def _faltas():
    return cache.estatisticas()["faltas"]


def test_segunda_consulta_vem_do_cache_e_e_uma_copia(carregado):
    a = ind.kpis(AGO)
    f = _faltas()
    b = ind.kpis(AGO)
    assert _faltas() == f and a == b
    b["faltas"] = 999                       # alterar o resultado não estraga o que está guardado
    assert ind.kpis(AGO)["faltas"] == a["faltas"]


def test_escrita_pelo_site_limpa_o_cache(carregado):
    antes = ind.kpis(AGO)["nc_periodo"]
    with transacao() as c:
        c.execute(text("""INSERT INTO nao_conformidades (data, categoria, problema, etapa)
                          VALUES ('2026-08-10', 'Teste', 'teste de cache', 'PRE_ANALITICA')"""))
    try:
        assert ind.kpis(AGO)["nc_periodo"] == antes + 1
    finally:
        with transacao() as c:
            c.execute(text("DELETE FROM nao_conformidades WHERE problema = 'teste de cache'"))


def test_recalculo_da_camada_analitica_muda_a_versao(carregado):
    v1 = cache._versao()
    ind.kpis(AGO)
    with carregado["engine"].begin() as c:     # como a importação feita no seu computador
        c.execute(text("SELECT analitico.atualizar()"))
    assert cache._versao() != v1
    f = _faltas()
    ind.kpis(AGO)
    assert _faltas() == f + 1


def test_jornada_e_faltas_repetidas(carregado):
    j = ind.jornada_paciente(AGO)
    assert [e["etapa"] for e in j] == ["Agendados", "Com registro", "Compareceram", "Atendidos no laboratório"]
    assert j[0]["pct_total"] == 100.0
    assert all(a["quantidade"] >= b["quantidade"] for a, b in zip(j, j[1:]))   # funil nunca cresce
    r = ind.faltas_repetidas(AGO)
    assert r["faltas"] == sum(f["faltas"] for f in r["faixas"]) == ind.kpis(AGO)["faltas"]
    m = ind.evolucao_mensal()
    assert all(abs(x["taxa_falta"] + x["taxa_comparecimento"] - 100) < 0.2 for x in m if x["taxa_falta"] is not None)


def test_destaques_ubs_e_numeros_do_periodo(carregado):
    d = ind.destaques_ubs(AGO, minimo=1)
    taxas = [r["taxa_comparecimento"] for r in d["comparecimento"]]
    assert taxas and taxas == sorted(taxas, reverse=True)
    assert all(r["variacao_pp"] < 0 for r in d["melhoraram"])
    nums = {n["rotulo"]: n for n in ind.numeros_periodo(AGO)}
    assert "UBS com mais agendamentos" in nums     # 01/08 é sábado: sem dia útil
    assert "Horário mais disputado" not in nums and "Faltas de quem já faltou" in nums   # todos às 07:00 = horário único
    h = ind.horario_unico(AGO)
    assert h["horario"] == "07:00" and h["pct"] == 100.0 and h["n_outros"] == 0
    assert nums["Exame mais realizado"]["valor"]


def test_kpis_da_agenda_por_ubs(carregado):
    rede = ind.kpis_agenda(AGO)
    ubs = ind.kpis_agenda(AGO, 1)
    assert ubs["agendamentos"] <= rede["agendamentos"] and ubs["faltas"] <= rede["faltas"]
    assert rede["agendamentos"] == ind.kpis(AGO)["agendamentos"]
    c = ind.kpis_agenda_comparados(AGO, 1)
    assert set(c) == {"atual", "anterior", "variacao", "periodo_anterior"}
