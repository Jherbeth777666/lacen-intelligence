"""Testa os arquivos ANTES de importar — faz tudo o que o site faria, mas não grava nada.

Uso (na pasta do projeto):
  python -m scripts.verificar_planilhas "C:\\Users\\Jhon\\projeto_bi\\saida_bi" Setembro
      → pega os arquivos da pasta com "Setembro" no nome + o dUnidades
  python -m scripts.verificar_planilhas "agenda.xlsx" "worklab.xls"
      → arquivos avulsos

Mostra só contagens, nomes de colunas, UBS e exames. Nenhum nome, CPF ou CNS de paciente aparece.
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from etl.importador import Relatorio, mapear_colunas, tratar_agendamentos, tratar_worklab  # noqa: E402
from etl.lote import NOMES_PAPEL, _estrategia, historico_do_banco, montar  # noqa: E402

LINHA = "-" * 76
EXT = (".csv", ".xlsx", ".xls", ".txt")


def _arquivos(args) -> list[pathlib.Path]:
    if len(args) >= 1 and pathlib.Path(args[0]).is_dir():
        pasta, filtro = pathlib.Path(args[0]), (args[1].lower() if len(args) > 1 else "")
        return sorted(p for p in pasta.iterdir() if p.suffix.lower() in EXT
                      and (filtro in p.stem.lower() or p.stem.lower().startswith(("dunidades", "unidades"))))
    return [pathlib.Path(a) for a in args]


def _simular(df, fonte, conn) -> None:
    nome = "AGENDA" if fonte == "AGENDAMENTOS" else "WORKLAB (exames + atendimentos)"
    print(LINHA); print(f"SIMULAÇÃO DA IMPORTAÇÃO — {nome}")
    tabela, _ = mapear_colunas(df, fonte)
    rel = Relatorio(fonte=fonte, arquivo="-", hash_arquivo="-")
    rel.min_freq_exame_novo = 1               # igual à importação em lote do site
    trans = conn.begin()
    try:
        tratar = tratar_agendamentos if fonte == "AGENDAMENTOS" else tratar_worklab
        limpo = tratar(tabela, rel, conn, "verificacao", True, _estrategia(df), date.today())
    finally:
        trans.rollback()                      # nada é gravado
    rej = {e["linha"] for e in rel.erros if e["severidade"] == "REJEITADA"}
    print(f"  {len(tabela)} linhas  ·  {len(limpo)} entrariam  ·  {len(rej)} seriam rejeitadas")
    if len(limpo):
        print(f"  Período: {min(limpo['data']):%d/%m/%Y} a {max(limpo['data']):%d/%m/%Y}   ·   por mês: "
              + ", ".join(f"{m}={n}" for m, n in sorted(Counter(d.strftime('%m/%Y') for d in limpo['data']).items(),
                                                       key=lambda x: x[0][3:] + x[0][:2])))
    if fonte == "AGENDAMENTOS" and len(limpo):
        st = Counter(limpo["status"])
        real = st.get("COMPARECEU", 0) + st.get("FALTOU", 0)
        print("  Status: " + ", ".join(f"{k}={n}" for k, n in st.most_common())
              + (f"  ·  taxa de faltas {100 * st.get('FALTOU', 0) / real:.1f}%" if real else ""))
    if fonte == "WORKLAB" and len(limpo):
        print(f"  Atendimentos: {limpo['codigo_worklab'].nunique()}  ·  exames: {len(limpo)}")
    for m, n in Counter(e["problema"] for e in rel.erros if e["severidade"] == "REJEITADA").most_common(8):
        print(f"  [rejeição] {n:>6} x {m}")
    for e in rel.erros:
        if e["linha"] is None and e["campo"] in ("ubs", "exame", "status"):
            print(f"  [{'info' if e.get('info') else 'aviso'}] {e['valor']}: {e['problema']}")


def main(*args):
    if not args:
        sys.exit(__doc__)
    caminhos = _arquivos(args)
    faltando = [str(c) for c in caminhos if not c.exists()]
    if faltando or not caminhos:
        sys.exit(f"[ERRO] não encontrei: {', '.join(faltando) or 'nenhum arquivo com esse filtro na pasta'}. "
                 "Confira o caminho (use aspas).")
    engine = None
    try:
        from app.config import Config
        from app.db import criar_engine
        engine = criar_engine(Config.DATABASE_URL)
        historico = historico_do_banco(engine, Config.PSEUDO_SECRET)
    except Exception as e:
        print(f"Sem conexão com o banco ({type(e).__name__}): verifico só os arquivos.")
        engine, historico = None, None
    lote = montar([(c.name, c.read_bytes()) for c in caminhos], historico)
    print(LINHA); print("ARQUIVOS")
    for a in lote.arquivos:
        print(f"  {a.nome}  ->  {NOMES_PAPEL[a.papel].upper()}" + (f"   [ERRO] {a.erro}" if a.erro else ""))
        if a.df is not None:
            print(f"      {len(a.df)} linhas · colunas: {', '.join(str(c) for c in a.df.columns[:20])}")
    if lote.notas or lote.problemas:
        print(LINHA); print("COMO OS ARQUIVOS FORAM JUNTADOS")
        for n in lote.notas:
            print(f"  [ok] {n}")
        for p in lote.problemas:
            print(f"  [ERRO] {p}")
    if lote.opcoes:
        print(LINHA); print("IDENTIFICAÇÃO DO PACIENTE — opções avaliadas (a primeira é a escolhida)")
        print(f"  {'agenda':<16}{'worklab':<16}{'cruzamento':>11}{'histórico agenda':>18}{'histórico worklab':>19}")
        for o in lote.opcoes[:6]:
            h = lambda v: "-" if v is None else f"{v:.0f}%"  # noqa: E731
            print(f"  {o['col_a'][:15]:<16}{o['col_w'][:15]:<16}{o['cruz']:>10.0f}%{h(o['ha']):>18}{h(o['hw']):>19}")
    if engine is None:
        return
    conn = engine.connect()
    for df, fonte in ((lote.agenda, "AGENDAMENTOS"), (lote.worklab, "WORKLAB")):
        if df is not None:
            _simular(df, fonte, conn)
    conn.close()
    print(LINHA)
    print("Nada foi gravado. Se não houver [ERRO], pode importar no site (Importação → selecionar os mesmos arquivos).")


if __name__ == "__main__":
    main(*sys.argv[1:])
