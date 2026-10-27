"""Importa os dados de um painel Power BI (.pbix) pelo ETL do LACEN Intelligence.

Lê fAgendamentos_UBS, fAtendimentos_Worklab e fExames_Worklab (e dUnidades, no formato antigo).
Nomes e códigos de paciente nunca são gravados em claro: o ETL aplica HMAC (pseudonimização).
A coluna Observações (texto livre com dados de saúde) não é lida.

Uso (em um banco SEPARADO do de demonstração):
  python -m scripts.importar_pbix "Painel.pbix"
  python -m scripts.importar_pbix "Painel.pbix" --refazer-worklab   (apaga o Worklab já importado e importa de novo)
Requer: pip install pbixray
"""
import pathlib
import sys

import pandas as pd
from sqlalchemy import create_engine

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.config import Config  # noqa: E402
from app.db import criar_engine  # noqa: E402
from etl.importador import ArquivoJaImportado, importar  # noqa: E402


def _data(serie: pd.Series) -> pd.Series:
    """Converte qualquer formato de data (datetime, texto ISO ou dd/mm/aaaa) para dd/mm/aaaa."""
    def um(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        if isinstance(v, str):
            v = v.strip()
            iso = len(v) >= 10 and v[4:5] == "-" and v[:4].isdigit()
            t = pd.to_datetime(v, errors="coerce", dayfirst=not iso)
        else:
            t = pd.to_datetime(v, errors="coerce")
        return None if pd.isna(t) else t.strftime("%d/%m/%Y")
    return serie.map(um)


def _hora(serie: pd.Series) -> pd.Series:
    """Converte datetime, time, timedelta ou texto para HH:MM:SS."""
    def um(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        if hasattr(v, "strftime"):
            return v.strftime("%H:%M:%S")
        if isinstance(v, pd.Timedelta):
            t = int(v.total_seconds())
            return f"{t // 3600 % 24:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
        t = pd.to_datetime(str(v), errors="coerce")
        return None if pd.isna(t) else t.strftime("%H:%M:%S")
    return serie.map(um)


def _col(df: pd.DataFrame, *nomes):
    """Primeira coluna existente entre os nomes dados (ou None)."""
    for n in nomes:
        if n in df.columns:
            return n
    return None


def limpar_worklab(eng) -> None:
    """Apaga só o que veio de importações WORKLAB (atendimentos, exames e o histórico delas)."""
    from sqlalchemy import text
    with eng.begin() as c:
        n = c.execute(text("""DELETE FROM atendimentos WHERE importacao_id IN
                              (SELECT id FROM importacoes WHERE fonte = 'WORKLAB')""")).rowcount
        c.execute(text("DELETE FROM importacoes WHERE fonte = 'WORKLAB'"))
    print(f"• Worklab anterior removido ({n} atendimentos). Agendamentos preservados.")
    from etl.importador import atualizar_camada_analitica
    atualizar_camada_analitica(eng)


def main(caminho: str, refazer_worklab: bool = False):
    from pbixray import PBIXRay

    m = PBIXRay(caminho)
    tabelas = set(m.tables)
    print("Tabelas encontradas:", ", ".join(sorted(t for t in tabelas if not t.startswith(("Date", "LocalDate")))))
    fontes = []
    ids_ag = ids_wl = set()

    # Agendamentos
    if "fAgendamentos_UBS" in tabelas:
        ag = m.get_table("fAgendamentos_UBS")
        if "Unidade" in ag.columns:                       # formato novo: nome da UBS direto
            ubs = ag["Unidade"]
        elif "ID_Unidade" in ag.columns and "dUnidades" in tabelas:   # formato antigo: id + dimensão
            ubs = ag["ID_Unidade"].map(m.get_table("dUnidades").set_index("ID_Unidade")["Unidade"])
        else:
            sys.exit(f"fAgendamentos_UBS sem coluna de unidade. Colunas: {list(ag.columns)}")
        col_pac = _col(ag, "ID_Paciente", "Paciente")     # o ETL pseudonimiza (HMAC); nada é guardado em claro
        ag_bruto = pd.DataFrame({
            "Data": _data(ag["Data"]),
            "Horário": _hora(ag["Horário"]),
            "UBS": ubs,
            "ID_Paciente": ag[col_pac],
            "Tipo": ag["Tipo"],
            "Status": ag["Status"],
        })
        ids_ag = set(ag[col_pac].dropna().astype(str))
        fontes.append(("AGENDAMENTOS", ag_bruto, "pbix_agendamentos.csv"))
    else:
        print("• Este arquivo não tem agendamentos: importando só o Worklab.")

    # Worklab
    if {"fAtendimentos_Worklab", "fExames_Worklab"} <= tabelas:
        at = m.get_table("fAtendimentos_Worklab")
        ex = m.get_table("fExames_Worklab")
        candidatas = [c for c in ("ID_Paciente", "Paciente") if c in at.columns]
        if not candidatas:
            sys.exit(f"fAtendimentos_Worklab sem coluna de paciente. Colunas: {list(at.columns)}")
        # usa a coluna que mais coincide com os agendamentos (assim o cruzamento funciona)
        col_at = max(candidatas, key=lambda c: len(ids_ag & set(at[c].dropna().astype(str)))) if ids_ag else candidatas[0]
        print(f"• Worklab: identificador do paciente = coluna '{col_at}'")
        pac_por_atend = at.drop_duplicates("ID_Atendimento").set_index("ID_Atendimento")[col_at]
        wl_bruto = pd.DataFrame({
            "Atendimento": ex["ID_Atendimento"].astype(str),
            "Data Atendimento": _data(ex["Data_Atendimento"]),
            "ID_Paciente": ex["ID_Atendimento"].map(pac_por_atend),
            "Sigla Exame": ex["Sigla_Exame"],
        })
        ids_wl = set(at[col_at].dropna().astype(str))
        fontes.append(("WORKLAB", wl_bruto, "pbix_worklab.csv"))

    if ids_ag and ids_wl:
        comuns = len(ids_ag & ids_wl)
        print(f"• Pacientes em comum entre agendamentos e Worklab: {comuns} "
              f"(de {len(ids_ag)} e {len(ids_wl)})")
        if comuns == 0:
            print("  ⚠ Nenhum identificador coincide: o cruzamento agendamento × atendimento não vai funcionar "
                  "com este arquivo (os dois lados usam identificadores diferentes).")

    if not fontes:
        sys.exit("Nenhuma tabela conhecida encontrada no arquivo.")

    eng = criar_engine(Config.DATABASE_URL)
    if refazer_worklab:
        limpar_worklab(eng)
    for fonte, df, nome in fontes:
        conteudo = df.to_csv(index=False, sep=";").encode()
        try:
            rel = importar(eng, fonte, conteudo, nome, segredo=Config.PSEUDO_SECRET, criar_ubs=True,
                           estrategia=("codigo",), df=df.astype(object))
        except ArquivoJaImportado:
            print(f"• {nome}: já importado antes — mantido como está.")
            continue
        r = rel.resumo()
        print(f"✓ {nome}: lidas={r['linhas_lidas']} inseridas={r['inseridas']} duplicadas={r['duplicadas']} "
              f"rejeitadas={r['rejeitadas']} avisos={r['avisos']}")
        resumo = {}
        for e in rel.erros:
            resumo[e["problema"]] = resumo.get(e["problema"], 0) + 1
        for p, n in sorted(resumo.items(), key=lambda x: -x[1]):
            print(f"     {n:>5} × {p}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    main(args[0], refazer_worklab="--refazer-worklab" in sys.argv)