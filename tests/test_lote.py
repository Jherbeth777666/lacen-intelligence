"""Importação em lote no formato da pasta saida_bi: agenda + atendimentos + exames + dUnidades."""
from datetime import date

import pytest
from sqlalchemy import text

from etl.importador import atualizar_camada_analitica
from etl.lote import importar_lote, montar

HOJE = date(2026, 10, 6)


def _arquivos():
    uni = "ID_Unidade;Unidade\n1;UBS ALFA\n2;UBS BETA\n"
    ag = ["Data;Horário;ID_Unidade;ID_Paciente;Tipo;Status;Observações"]
    at = ["ID_Atendimento,ID_Paciente,Paciente,Data_Atendimento"]
    ex = ["ID_Atendimento;Data_Atendimento;Sigla_Exame;Valor"]
    for i in range(40):
        dia = 2 + i % 20
        status = "Faltou" if i % 4 == 0 else "Compareceu"
        ag.append(f"2025-09-{dia:02d};07:{'30' if i % 2 else '00'}:00;{1 + i % 2};P{i:04d};Exame de Rotina;{status};obs {i}")
        if status == "Compareceu":
            # no Worklab, ID_Paciente é um número interno; o código da agenda está na coluna "Paciente"
            at.append(f"{5000 + i},{7700 + i},P{i:04d},2025-09-{dia:02d}")
            for sig in ("HEMO", "GLI"):
                ex.append(f"{5000 + i};{dia:02d}/09/2025;{sig};4,11")
    return [("dUnidades.csv", uni.encode()), ("fAgendamentos_Setembro.csv", "\n".join(ag).encode("cp1252")),
            ("fAtendimentos_Setembro.csv", "\n".join(at).encode()), ("fExames_Setembro.csv", "\n".join(ex).encode())]


def test_reconhece_cada_arquivo_e_escolhe_a_identificacao():
    lote = montar(_arquivos())
    assert {a.nome: a.papel for a in lote.arquivos} == {
        "dUnidades.csv": "UNIDADES", "fAgendamentos_Setembro.csv": "AGENDAMENTOS",
        "fAtendimentos_Setembro.csv": "ATENDIMENTOS", "fExames_Setembro.csv": "EXAMES"}
    assert not lote.problemas
    assert lote.identificacao == "ID do paciente" and lote.cruzamento_pct == 100
    assert set(lote.agenda["Unidade"]) == {"UBS ALFA", "UBS BETA"}          # veio do dUnidades
    assert "ID_Paciente" in lote.worklab.columns and "Paciente" not in lote.worklab.columns
    assert lote.worklab["ID_Paciente"].iloc[0].startswith("P")              # coluna "Paciente" do Worklab


def test_falta_dunidades_ou_exames_avisa():
    arqs = [a for a in _arquivos() if a[0] not in ("dUnidades.csv", "fExames_Setembro.csv")]
    lote = montar(arqs)
    texto = " ".join(lote.problemas)
    assert "dUnidades" in texto and "fExames" in texto


def test_importa_setembro_inteiro_e_cruza(carregado):
    eng = carregado["engine"]
    lote, res = importar_lote(eng, _arquivos(), segredo="segredo-de-teste", hoje=HOJE)
    rels = dict(res)
    try:
        assert rels["AGENDAMENTOS"].inseridas == 40 and rels["AGENDAMENTOS"].rejeitadas == 0
        assert rels["WORKLAB"].rejeitadas == 0 and rels["WORKLAB"].inseridas == 60
        with eng.connect() as c:
            r = c.execute(text("""SELECT sum(compareceu), count(*) FILTER (WHERE atendido_no_lab)
                                  FROM analitico.fato_agendamentos WHERE data BETWEEN '2025-09-01' AND '2025-09-30'""")).first()
        assert r[0] == 30 and r[1] == 30                                    # todos que compareceram foram atendidos
        # reenviar os mesmos arquivos: bloqueado (já importados sem rejeição)
        _, res2 = importar_lote(eng, _arquivos(), segredo="segredo-de-teste", hoje=HOJE)
        assert all(type(x).__name__ == "ArquivoJaImportado" for _, x in res2)
    finally:
        with eng.begin() as c:
            for rel in rels.values():
                if hasattr(rel, "importacao_id"):
                    c.execute(text("DELETE FROM agendamentos WHERE importacao_id=:i"), {"i": rel.importacao_id})
                    c.execute(text("DELETE FROM atendimentos WHERE importacao_id=:i"), {"i": rel.importacao_id})
        atualizar_camada_analitica(eng)


def test_historico_desempata_a_identificacao():
    """Nome cruza um pouco melhor no mês, mas só o ID reconhece os pacientes dos meses anteriores → usa ID."""
    import pandas as pd
    from etl.lote import Lote, _escolher_identificacao
    ag = pd.DataFrame({"Data": ["2025-09-02"] * 10, "Status": ["Compareceu"] * 10,
                       "ID_Paciente": [f"A{i}" for i in range(10)], "Paciente": [f"NOME {i}" for i in range(10)]})
    wl = pd.DataFrame({"ID_Atendimento": range(10), "ID_Paciente": [f"A{i}" if i < 8 else "X" for i in range(10)],
                       "Paciente": [f"NOME {i}" for i in range(10)], "Sigla_Exame": ["HEMO"] * 10})

    def historico(lado, tipo, chaves):          # só os IDs existiam em junho–agosto
        return 60.0 if any(k.startswith("A") for k in chaves) else 0.0
    lote = Lote([])
    (ta, ca), (tw, cw) = _escolher_identificacao(ag, wl, lote, historico)
    assert (ca, cw) == ("ID_Paciente", "ID_Paciente")
    lote2 = Lote([])
    (_, ca2), (_, cw2) = _escolher_identificacao(ag, wl, lote2, None)   # sem histórico: o melhor cruzamento
    assert (ca2, cw2) == ("Paciente", "Paciente")


def test_so_exames_usa_a_identificacao_que_bate_com_a_agenda_ja_importada():
    """Exames de maio enviados depois da agenda de maio: a chave certa é a que bate com a AGENDA do banco
    (o Worklab antigo quase não tem os mesmos pacientes)."""
    import pandas as pd
    from etl.lote import Lote, _escolher_identificacao
    wl = pd.DataFrame({"ID_Atendimento": range(10), "ID_Paciente": [f"{9000 + i}" for i in range(10)],
                       "Paciente": [f"NOME {i}" for i in range(10)], "Sigla_Exame": ["HEMO"] * 10})

    def historico(lado, tipo, chaves):
        if lado == "AGENDAMENTOS":            # a agenda de maio já está no banco, com os nomes
            return 90.0 if any(k.startswith("NOME") for k in chaves) else 0.0
        return 5.0 if any(k.startswith("9") for k in chaves) else 2.0   # Worklab antigo: quase nada
    lote = Lote([])
    ag, (tw, cw) = _escolher_identificacao(None, wl, lote, historico)
    assert ag is None and cw == "Paciente"
    assert "90%" in lote.notas[0]
