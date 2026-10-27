"""Importação por linha de comando (base para automação/agendamento futuro).

Exemplos:
  python -m etl.cli agendamentos data/raw/agendamentos_setembro.xlsx
  python -m etl.cli worklab data/raw/worklab_setembro.csv --criar-ubs
"""
import argparse
import pathlib
import sys

from sqlalchemy import create_engine

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.config import Config  # noqa: E402
from app.db import criar_engine  # noqa: E402
from etl.importador import ArquivoJaImportado, importar  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Importa arquivos para o LACEN Intelligence")
    ap.add_argument("fonte", choices=["agendamentos", "worklab"])
    ap.add_argument("arquivos", nargs="+", type=pathlib.Path)
    ap.add_argument("--criar-ubs", action="store_true", help="cadastra automaticamente UBS desconhecidas")
    args = ap.parse_args()

    eng = criar_engine(Config.DATABASE_URL)
    codigo_saida = 0
    for arq in args.arquivos:
        try:
            rel = importar(eng, args.fonte, arq.read_bytes(), arq.name,
                           segredo=Config.PSEUDO_SECRET, criar_ubs=args.criar_ubs)
        except ArquivoJaImportado as e:
            print(f"• {arq.name}: {e}")
            continue
        r = rel.resumo()
        print(f"✓ {arq.name}: lidas={r['linhas_lidas']} inseridas={r['inseridas']} duplicadas={r['duplicadas']} "
              f"rejeitadas={r['rejeitadas']} avisos={r['avisos']} período={r['periodo_inicio']}→{r['periodo_fim']}")
        problemas = {}
        for e in rel.erros:
            problemas[e["problema"]] = problemas.get(e["problema"], 0) + 1
        for p, n in sorted(problemas.items(), key=lambda x: -x[1])[:8]:
            print(f"    {n:>5} × {p}")
        if rel.rejeitadas:
            codigo_saida = 2
    sys.exit(codigo_saida)


if __name__ == "__main__":
    main()
