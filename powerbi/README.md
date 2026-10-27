# Painel Power BI

Abra `LACEN_Intelligence.pbip` no Power BI Desktop e clique em **Atualizar**.
Antes, gere os dados com `python -m scripts.exportar_powerbi`.

- `LACEN_Intelligence.SemanticModel/` — modelo (TMDL): tabelas, Power Query, relações e medidas.
- `LACEN_Intelligence.Report/` — relatório (PBIR): 5 páginas.
- `gerar_projeto.py` — gera os dois a partir do código (`python -m powerbi.gerar_projeto`).
- `esqueleto/` — base criada pela CLI oficial da Microsoft (`powerbi-report-author scaffold`).

Guia completo: [docs/POWERBI.md](../docs/POWERBI.md)
