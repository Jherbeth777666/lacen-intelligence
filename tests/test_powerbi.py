"""O projeto Power BI gerado é consistente: todo campo usado no relatório existe no modelo."""
import json
import re
import subprocess
import sys

from powerbi.gerar_projeto import MEDIDAS, TABELAS


def test_projeto_powerbi_consistente(tmp_path):
    r = subprocess.run([sys.executable, "-m", "powerbi.gerar_projeto", "--saida", str(tmp_path), "--pasta", "C:\\dados"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    colunas = {t: {c[1] for c in spec["cols"]} for t, spec in TABELAS.items()}
    medidas = {m[1] for m in MEDIDAS}
    assert not medidas & set(TABELAS), "medida com o mesmo nome de uma tabela"

    # DAX: toda referência Tabela[Coluna] e [Medida] existe
    for _, nome, dax, fmt, _ in MEDIDAS:
        assert fmt, nome
        for tab, col in re.findall(r"('(?:[^']|'')+'|[A-Za-zÀ-ú_][\wÀ-ú]*)\[([^\]]+)\]", dax):
            tab = tab.strip("'")
            assert col in colunas[tab], f"{nome}: {tab}[{col}]"
        for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", dax):
            assert ref in medidas, f"{nome}: [{ref}]"

    # relatório: todo campo existe
    visuais = list((tmp_path / "LACEN_Intelligence.Report").rglob("visual.json"))
    assert len(visuais) > 30
    for v in visuais:
        texto = v.read_text(encoding="utf-8")
        for m in re.finditer(r'"queryRef": "([^".]+)\.([^"]+)"', texto):
            tab, campo = m.groups()
            assert campo in (medidas if tab == "Medidas" else colunas[tab]), f"{v}: {tab}.{campo}"

    pbip = json.loads((tmp_path / "LACEN_Intelligence.pbip").read_text())
    assert pbip["artifacts"][0]["report"]["path"] == "LACEN_Intelligence.Report"
    expr = (tmp_path / "LACEN_Intelligence.SemanticModel" / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
    assert '"C:\\dados"' in expr
