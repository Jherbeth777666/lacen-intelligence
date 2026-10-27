"""Aplica as migrations e seeds em ordem, registrando o que já rodou.

Uso:  python -m scripts.migrar            (usa DATABASE_URL do .env)
      python -m scripts.migrar --recriar  (APAGA tudo e recria — só em banco local de desenvolvimento)

Banco que já tem as tabelas do projeto antigo (ex.: Supabase do TCC):
na primeira execução, as tabelas antigas com o mesmo nome (pacientes, triagens,
exames, usuarios…) são MOVIDAS para o schema `legado_tcc` — nada é apagado.
"""
import argparse
import pathlib
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.config import Config  # noqa: E402
from app.db import criar_engine  # noqa: E402

RAIZ = pathlib.Path(__file__).resolve().parents[1] / "db"
TABELAS_DO_SISTEMA = ["ubs", "ubs_alias", "setores", "exames", "exame_alias", "profissionais", "usuarios",
                      "pacientes", "triagens", "triagem_exames", "importacoes", "importacao_erros", "agendamentos",
                      "atendimentos", "atendimento_exames", "nao_conformidades", "planos_acao", "auditoria",
                      "ia_consultas", "medicos"]


def eh_supabase(url: str) -> bool:
    return "supabase" in url


def preservar_legado(c) -> None:
    """Move tabelas antigas de mesmo nome para o schema legado_tcc (só antes da 1ª migration)."""
    existentes = [r[0] for r in c.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = ANY(:t)"),
        {"t": TABELAS_DO_SISTEMA})]
    if not existentes:
        return
    c.execute(text("CREATE SCHEMA IF NOT EXISTS legado_tcc"))
    for t in existentes:
        if c.execute(text("SELECT to_regclass(:n)"), {"n": f"legado_tcc.{t}"}).scalar():
            c.execute(text(f'ALTER TABLE public."{t}" RENAME TO "{t}_antiga"'))
            t = f"{t}_antiga"
        c.execute(text(f'ALTER TABLE public."{t}" SET SCHEMA legado_tcc'))
    # O projeto antigo deixava essas tabelas abertas para a chave pública (anon): fechamos o acesso.
    for papel in ("anon", "authenticated"):
        if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": papel}).scalar():
            c.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA legado_tcc FROM {papel}"))
            c.execute(text(f"REVOKE ALL ON SCHEMA legado_tcc FROM {papel}"))
    for t in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='legado_tcc'")).scalars().all():
        c.execute(text(f'ALTER TABLE legado_tcc."{t}" ENABLE ROW LEVEL SECURITY'))
    print(f"ℹ  Tabelas do projeto antigo preservadas (e fechadas ao acesso público) em legado_tcc: {', '.join(existentes)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recriar", action="store_true", help="apaga schemas public/analitico antes")
    args = ap.parse_args()

    if args.recriar and eh_supabase(Config.DATABASE_URL):
        sys.exit("✗ --recriar é bloqueado no Supabase (apagaria o schema public do projeto). "
                 "Use apenas: python -m scripts.migrar")
    eng = criar_engine(Config.DATABASE_URL)
    with eng.begin() as c:
        if args.recriar:
            print("⚠  Recriando banco (public + analitico)…")
            c.execute(text("DROP SCHEMA IF EXISTS analitico CASCADE"))
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        c.execute(text("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            arquivo TEXT PRIMARY KEY, aplicado_em TIMESTAMPTZ DEFAULT now())"""))
        feitos = {r[0] for r in c.execute(text("SELECT arquivo FROM schema_migrations"))}
        if "migrations/001_schema.sql" not in feitos:
            preservar_legado(c)

    arquivos = sorted((RAIZ / "migrations").glob("*.sql")) + sorted((RAIZ / "seeds").glob("*.sql"))
    for arq in arquivos:
        chave = f"{arq.parent.name}/{arq.name}"
        if chave in feitos:
            continue
        print(f"→ {chave}")
        sql = arq.read_text(encoding="utf-8")
        raw = eng.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (arquivo) VALUES (%s)", (chave,))
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    print("✓ Banco atualizado.")


if __name__ == "__main__":
    main()
