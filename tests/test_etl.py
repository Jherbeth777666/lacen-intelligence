from datetime import date, time

import pytest
from sqlalchemy import text

from etl.anonimizacao import chave_paciente, pseudo_id
from etl.importador import atualizar_camada_analitica, ArquivoJaImportado, importar
from etl.normalizacao import cpf_valido, mapear_valor, normalizar_texto, parse_data, parse_hora

from .conftest import AGEND_CSV, HOJE


# ---------------- normalização ----------------
def test_normalizar_texto_unifica_grafias():
    assert normalizar_texto("  U.B.S. Vila  Esperança ") == "UBS VILA ESPERANCA"
    assert normalizar_texto(None) == ""


@pytest.mark.parametrize("entrada,esperado", [
    ("01/08/2026", date(2026, 8, 1)), ("2026-08-01", date(2026, 8, 1)), (46235, date(2026, 8, 1)),
    ("31/06/2026", None), ("", None), ("abc", None)])
def test_parse_data(entrada, esperado):
    assert parse_data(entrada) == esperado


def test_parse_hora_remove_ruido_de_ponto_flutuante():
    assert parse_hora("07:59:59.999") == time(8, 0)
    assert parse_hora("7h30") == time(7, 30)
    assert parse_hora(0.2916666) == time(7, 0)
    assert parse_hora("25:00") is None


def test_cpf_valido():
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("111.111.111-11")
    assert not cpf_valido("529.982.247-24")


def test_mapear_valor_aceita_prefixo():
    assert mapear_valor("Faltou sem aviso", {"FALTOU": "FALTOU"}) == "FALTOU"
    assert mapear_valor("talvez", {"FALTOU": "FALTOU"}) is None


# ---------------- anonimização ----------------
def test_pseudo_id_deterministico_e_dependente_do_segredo():
    a = pseudo_id("CPF:52998224725", "s1")
    assert a == pseudo_id("CPF:52998224725", "s1")
    assert a != pseudo_id("CPF:52998224725", "s2")
    assert a.startswith("PAC_") and "52998224725" not in a


def test_chave_paciente_prioridade():
    assert chave_paciente(cpf="52998224725", nome="X Y", nascimento=date(2000, 1, 1))[1] == "cpf"
    assert chave_paciente(cpf="123", nome="Maria Silva", nascimento=date(2000, 1, 1))[1] == "nome_nascimento"
    assert chave_paciente(cns="700000000000001")[1] == "cns"
    assert chave_paciente(nome="Maria")[0] is None


# ---------------- pipeline ----------------
def test_importacao_agendamentos(carregado):
    r = carregado["agend"]
    assert r.linhas_lidas == 8
    assert r.inseridas == 3               # 2 válidas UBS ALFA/ubs alfa + 1 BETA (a repetida é duplicada)
    assert r.duplicadas == 1
    problemas = {e["problema"] for e in r.erros}
    assert "Data inválida ou vazia" in problemas
    assert "Status desconhecido" in problemas
    assert any(p.startswith("UBS não cadastrada") for p in problemas)
    assert any(p.startswith("Paciente sem identificação") for p in problemas)
    assert any(p.startswith("Coluna de observações descartada") for p in problemas)


def test_nenhum_dado_pessoal_no_banco_analitico(carregado):
    with carregado["engine"].connect() as c:
        pseudos = [r[0] for r in c.execute(text("SELECT paciente_pseudo FROM agendamentos"))]
        erros = [r[0] or "" for r in c.execute(text("SELECT valor FROM importacao_erros"))]
    assert all(p.startswith("PAC_") for p in pseudos)
    assert not any("MARIA SILVA" in e or "700000000000005" in e for e in erros)  # valores pessoais mascarados


def test_mesmo_paciente_mesmo_codigo_entre_fontes(carregado):
    with carregado["engine"].connect() as c:
        cruzados = c.execute(text("""SELECT count(*) FROM analitico.fato_agendamentos
                                     WHERE atendido_no_lab""")).scalar()
    # Maria aparece nas duas fontes; o atendimento de Ana (A2) foi rejeitado por conflito de paciente
    assert cruzados == 1


def test_alias_de_ubs_aprendido(carregado):
    with carregado["engine"].connect() as c:
        aliases = {r[0] for r in c.execute(text("SELECT alias FROM ubs_alias"))}
    assert "UBS ALFA" not in aliases  # nome oficial não precisa de alias
    assert "UBS BETA" not in aliases and len(aliases) >= 0


def test_importacao_worklab_valida_conflitos(carregado):
    r = carregado["wl"]
    problemas = [e["problema"] for e in r.erros]
    assert "Mesmo atendimento com paciente ou data diferentes" in problemas
    assert "Exame não catalogado (sigla desconhecida)" in problemas
    assert "Exame repetido no mesmo atendimento" in problemas
    with carregado["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM atendimentos")).scalar() == 1   # só A1 é válido
        assert c.execute(text("SELECT count(*) FROM atendimento_exames")).scalar() == 2


def test_reimportar_arquivo_com_rejeicoes_nao_duplica(carregado):
    """Arquivo que teve linhas rejeitadas pode ser reenviado (ex.: depois de cadastrar a UBS); nada duplica."""
    with carregado["engine"].connect() as c:
        antes = c.execute(text("SELECT count(*) FROM agendamentos")).scalar()
    r = importar(carregado["engine"], "AGENDAMENTOS", AGEND_CSV.encode(), "copia.csv",
                 segredo="segredo-de-teste", hoje=HOJE)
    assert r.inseridas == 0 and r.importacao_id != carregado["agend"].importacao_id
    with carregado["engine"].connect() as c:
        assert c.execute(text("SELECT count(*) FROM agendamentos")).scalar() == antes


def test_reimportar_arquivo_limpo_e_bloqueado(carregado):
    limpo = "Data;Unidade;Paciente;Data Nascimento;Status\n03/08/2026;UBS ALFA;BIA LIMPA SOUZA;01/01/1991;Compareceu\n"
    r = importar(carregado["engine"], "AGENDAMENTOS", limpo.encode(), "limpo.csv", segredo="segredo-de-teste", hoje=HOJE)
    try:
        with pytest.raises(ArquivoJaImportado):
            importar(carregado["engine"], "AGENDAMENTOS", limpo.encode(), "limpo2.csv", segredo="segredo-de-teste", hoje=HOJE)
    finally:   # não deixa o registro de teste mexer nos números dos outros testes
        with carregado["engine"].begin() as c:
            c.execute(text("DELETE FROM agendamentos WHERE importacao_id = :i"), {"i": r.importacao_id})
        atualizar_camada_analitica(carregado["engine"])
