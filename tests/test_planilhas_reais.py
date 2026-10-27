"""Planilhas como os sistemas exportam de verdade: título em cima, data com hora, .xls, "xls" em HTML,
colunas com nomes diferentes, exame novo e identificação do paciente diferente entre agenda e Worklab."""
import io
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from etl.importador import ColunasAusentes, atualizar_camada_analitica, importar, ler_arquivo

HOJE = date(2026, 10, 6)
SEG = "segredo-de-teste"

PACIENTES = [  # nome, nascimento, cpf
    ("CARLOS ALBERTO NUNES", "10/02/1975", "52998224725"),
    ("FERNANDA LIMA ROCHA", "22/07/1988", "11144477735"),
    ("JOSE RIBAMAR COSTA", "05/11/1960", "39053344705"),
    ("LUCIA HELENA PRADO", "30/03/1952", "06208474090"),
]


def _xlsx_agenda() -> bytes:
    """Relatório com 3 linhas de título, coluna Data/Hora e situação por extenso (inclui cancelado)."""
    linhas = [["SECRETARIA MUNICIPAL DE SAÚDE", None, None, None, None, None, None],
              ["Relatório de agendamentos — setembro/2025", None, None, None, None, None, None],
              [None] * 7,
              ["Data/Hora do Agendamento", "Unidade de Saúde", "Nome do Paciente", "Data de Nascimento", "CPF",
               "Situação", "Observação"]]
    sit = ["Compareceu", "Não compareceu", "Compareceu", "Cancelado"]
    for i, (n, nasc, cpf) in enumerate(PACIENTES):
        linhas.append([datetime(2025, 9, 2 + i, 7, 30), "U.B.S. Alfa", n, nasc, cpf, sit[i], ""])
    linhas.append(["Total: 4", None, None, None, None, None, None])
    b = io.BytesIO()
    pd.DataFrame(linhas).to_excel(b, header=False, index=False)
    return b.getvalue()


def _html_worklab() -> bytes:
    """'xls' que na verdade é HTML (comum em sistemas web); só nome + nascimento, sem CPF; exame novo."""
    linhas = []
    for i, (n, nasc, _) in enumerate(PACIENTES[:3]):
        if i == 1:                      # Fernanda faltou: não tem atendimento
            continue
        for sig, nome in (("HEMO", "Hemograma"), ("ZNOVO", "Exame Novíssimo")):
            linhas.append((f"9{i}001", f"0{2 + i}/09/2025 08:10", n, nasc, sig, nome, "Liberado"))
    linhas.append(("90009", "03/09/2025 09:00", "OUTRO PACIENTE TESTE", "01/01/2001", "ZNOVO", "Exame Novíssimo", "Liberado"))
    corpo = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in l)}</tr>" for l in linhas)
    html = ("<html><body><h3>Produção por atendimento</h3><table border=1>"
            "<tr><th>Nº Atendimento</th><th>Data da Coleta</th><th>Paciente</th><th>Nascimento</th><th>Sigla</th>"
            f"<th>Exame</th><th>Situação</th></tr>{corpo}</table></body></html>")
    return html.encode("cp1252")


@pytest.fixture
def limpar(carregado):
    ids = []
    yield ids
    with carregado["engine"].begin() as c:
        for i in ids:
            c.execute(text("DELETE FROM agendamentos WHERE importacao_id = :i"), {"i": i})
            c.execute(text("DELETE FROM atendimentos WHERE importacao_id = :i"), {"i": i})
    atualizar_camada_analitica(carregado["engine"])


def test_le_relatorio_com_titulo_e_data_hora():
    df = ler_arquivo(_xlsx_agenda(), "agenda.xlsx", "AGENDAMENTOS")
    assert list(df.columns)[:2] == ["Data/Hora do Agendamento", "Unidade de Saúde"]
    assert len(df) == 5                                   # 4 pacientes + linha "Total"


def test_le_xls_antigo():
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook(); ws = wb.add_sheet("Plan1")
    for j, c in enumerate(["Data", "Unidade", "Paciente", "Status"]):
        ws.write(0, j, c)
    ws.write(1, 0, "01/09/2025"); ws.write(1, 1, "UBS ALFA"); ws.write(1, 2, "FULANO DE TAL"); ws.write(1, 3, "Faltou")
    b = io.BytesIO(); wb.save(b)
    df = ler_arquivo(b.getvalue(), "agenda.xls", "AGENDAMENTOS")
    assert df.iloc[0]["Status"] == "Faltou"


def test_agenda_e_worklab_reais_cruzam_pelo_nome(carregado, limpar):
    eng = carregado["engine"]
    ra = importar(eng, "AGENDAMENTOS", _xlsx_agenda(), "agenda_set.xlsx", segredo=SEG, hoje=HOJE)
    limpar.append(ra.importacao_id)
    assert ra.inseridas == 3                              # cancelado e linha "Total" ficam fora
    assert ra.rejeitadas == 1                             # só a linha "Total: 4" (data inválida)
    textos = " | ".join(e["problema"] for e in ra.erros)
    assert "cancelados" in textos and "Colunas usadas" in textos
    with eng.connect() as c:
        horas = {r[0] for r in c.execute(text("SELECT horario FROM agendamentos WHERE importacao_id=:i"),
                                          {"i": ra.importacao_id})}
    assert horas == {datetime(2025, 9, 2, 7, 30).time()}  # hora aproveitada da coluna Data/Hora

    rw = importar(eng, "WORKLAB", _html_worklab(), "producao_set.xls", segredo=SEG, hoje=HOJE)
    limpar.append(rw.importacao_id)
    assert rw.rejeitadas == 0
    assert any("Exame novo cadastrado automaticamente no catálogo (3 linhas)" in e["problema"] for e in rw.erros)
    assert any(e["problema"].startswith("Identificação do paciente: nome + data de nascimento") for e in rw.erros)
    with eng.connect() as c:
        cruzados = c.execute(text("""SELECT count(*) FROM analitico.fato_agendamentos
                                     WHERE data BETWEEN '2025-09-01' AND '2025-09-30' AND atendido_no_lab""")).scalar()
    assert cruzados == 2                                  # Carlos e José compareceram e foram atendidos


def test_coluna_faltando_explica_o_que_falta():
    csv = "Dia;Paciente;Situação\n01/09/2025;FULANO DE TAL;Compareceu\n".encode()
    from etl.importador import mapear_colunas
    with pytest.raises(ColunasAusentes) as e:
        mapear_colunas(ler_arquivo(csv, "x.csv", "AGENDAMENTOS"), "AGENDAMENTOS")
    assert "UBS/unidade" in str(e.value) and "“Dia”" in str(e.value)


def test_tela_de_importacao_explica_arquivo_ilegivel(cliente):
    from tests.conftest import logar
    logar(cliente, "admin")
    cliente.get("/importacao/")
    with cliente.session_transaction() as sess:
        token = sess["_csrf"]
    r = cliente.post("/importacao/", data={"_csrf": token, "fonte": "AGENDAMENTOS",
                                           "arquivo": (io.BytesIO(b"PK\x03\x04 isto nao e um excel"), "setembro.xlsx")},
                     content_type="multipart/form-data", follow_redirects=True)
    assert "Não consegui abrir a planilha" in r.get_data(as_text=True)
