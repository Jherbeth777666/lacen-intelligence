"""Exporta a camada analítica (sem dados pessoais) para CSV — fonte do projeto Power BI em powerbi/.

Uso: python -m scripts.exportar_powerbi [pasta_saida]   (padrão: data/powerbi)

Formato (o Power Query do projeto depende dele):
  • separador ";", UTF-8, cabeçalho na 1ª linha;
  • número com ponto decimal (12.5), inteiro sem ".0";
  • data AAAA-MM-DD, data e hora AAAA-MM-DD HH:MM:SS no horário de Brasília;
  • verdadeiro/falso como True/False; vazio = sem valor.
"""
import datetime as dt
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.config import Config  # noqa: E402
from app.db import criar_engine  # noqa: E402

VIEWS = ["dim_ubs", "dim_exame", "dim_calendario", "fato_agendamentos", "fato_atendimentos", "fato_exames",
         "fato_triagens", "fato_nao_conformidades", "kpi_ubs_mes"]

FUSO = "America/Sao_Paulo"


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        s = df[c]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            df[c] = s.dt.tz_convert(FUSO).dt.strftime("%Y-%m-%d %H:%M:%S")
        elif pd.api.types.is_datetime64_any_dtype(s):
            df[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
        elif pd.api.types.is_float_dtype(s):
            v = s.dropna()
            if len(v) and (v == v.round()).all() and c.endswith(("_id", "qtd_exames", "dias_resolucao")):
                df[c] = s.astype("Int64")          # 6.0 → 6 (inteiros que vieram como float por causa de nulos)
        elif s.dtype == object:
            amostra = s.dropna().head(50)
            if len(amostra) and all(isinstance(x, dt.datetime) for x in amostra):
                df[c] = pd.to_datetime(s, utc=True).dt.tz_convert(FUSO).dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


def main(saida: str = "data/powerbi"):
    pasta = pathlib.Path(saida)
    pasta.mkdir(parents=True, exist_ok=True)
    eng = criar_engine(Config.DATABASE_URL)
    for v in VIEWS:
        df = _normalizar(pd.read_sql(f"SELECT * FROM analitico.{v}", eng))
        df.to_csv(pasta / f"{v}.csv", sep=";", index=False, encoding="utf-8", decimal=".")
        print(f"✓ {v}.csv  ({len(df)} linhas)")
    print(f"\nPasta: {pasta.resolve()}")


if __name__ == "__main__":
    main(*sys.argv[1:])
