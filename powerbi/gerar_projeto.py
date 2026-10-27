"""Gera o projeto Power BI (PBIP) do LACEN Intelligence: modelo semântico em TMDL + relatório em PBIR.

Uso (na raiz do projeto):
    python -m scripts.exportar_powerbi          # gera os CSV em data/powerbi
    python -m powerbi.gerar_projeto             # gera powerbi/LACEN_Intelligence.pbip apontando para data/powerbi
    python -m powerbi.gerar_projeto --pasta "D:\\outra\\pasta"

Depois: abra powerbi/LACEN_Intelligence.pbip no Power BI Desktop → Atualizar.
Para virar .pbix: Arquivo → Salvar como → Arquivo do Power BI (.pbix).

O modelo lê SOMENTE os CSV exportados da camada analítica (sem nome, CPF, CNS ou endereço).
As definições dos indicadores são as mesmas do site (app/services/indicadores.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "powerbi"
NOME = "LACEN_Intelligence"
SM = f"{NOME}.SemanticModel"
RP = f"{NOME}.Report"

T = "\t"
VC = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json"


def hexid(*partes: str) -> str:
    return hashlib.sha1("|".join(partes).encode()).hexdigest()[:20]


def guid(*partes: str) -> str:
    h = hashlib.sha1(("guid|" + "|".join(partes)).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def q(nome: str) -> str:
    """Nome TMDL: entre aspas simples quando tiver espaço/acentos/símbolos."""
    if nome.replace("_", "").isalnum() and nome.isascii() and not nome[0].isdigit():
        return nome
    return "'" + nome.replace("'", "''") + "'"


def dax_tab(nome: str) -> str:
    return q(nome) if q(nome) != nome else nome


# =============================================================================
# MODELO SEMÂNTICO
# =============================================================================
# (coluna no CSV, nome no modelo, tipo, opções)
#   tipo: int64 | string | decimal | double | dateTime(data) | dateTimeFull | time | boolean
#   opções: h=oculta, k=chave, sort=<coluna>, fmt=<formato>
TABELAS: dict[str, dict] = {
    "Calendário": {
        "csv": "dim_calendario", "desc": "Uma linha por dia. Tabela de datas do modelo.", "data": True,
        "cols": [
            ("data", "Data", "date", {"k": 1, "fmt": "dd/mm/yyyy"}),
            ("ano", "Ano", "int64", {"fmt": "0"}),
            ("mes", "Mês Nº", "int64", {"h": 1, "fmt": "0"}),
            ("ano_mes", "Ano-Mês", "string", {}),
            ("mes_nome", "Mês", "string", {"sort": "Mês Nº"}),
            ("num_dia_semana", "Dia da Semana Nº", "int64", {"h": 1, "fmt": "0"}),
            ("dia_semana", "Dia da Semana", "string", {"sort": "Dia da Semana Nº"}),
            ("dia_util", "Dia Útil", "boolean", {}),
        ],
    },
    "UBS": {
        "csv": "dim_ubs", "desc": "Unidades de saúde (UBS e outros pontos de coleta).",
        "cols": [
            ("ubs_id", "ubs_id", "int64", {"h": 1, "k": 1}),
            ("ubs", "UBS", "string", {}),
            ("tipo", "Tipo de Unidade", "string", {}),
            ("ativo", "Ativa", "boolean", {}),
        ],
    },
    "Exame": {
        "csv": "dim_exame", "desc": "Catálogo de exames com setor e prazo de liberação.",
        "cols": [
            ("exame_id", "exame_id", "int64", {"h": 1, "k": 1}),
            ("sigla", "Sigla", "string", {}),
            ("exame", "Exame", "string", {}),
            ("setor", "Setor", "string", {}),
            ("valor_ref", "Valor de Referência", "decimal", {"fmt": "#,##0.00"}),
            ("prazo_horas", "Prazo (h)", "double", {"fmt": "0"}),
        ],
    },
    "Agendamentos": {
        "csv": "fato_agendamentos",
        "desc": "Agendamentos das UBS. 'realizado' = compareceu ou faltou (base da taxa de faltas).",
        "cols": [
            ("agendamento_id", "agendamento_id", "int64", {"h": 1}),
            ("data", "data", "date", {"h": 1}),
            ("horario", "Horário", "time", {"fmt": "hh:nn"}),
            ("ubs_id", "ubs_id", "int64", {"h": 1}),
            ("paciente_pseudo", "paciente_pseudo", "string", {"h": 1}),
            ("tipo", "Tipo", "string", {}),
            ("status", "Status", "string", {}),
            ("compareceu", "compareceu", "int64", {"h": 1}),
            ("faltou", "faltou", "int64", {"h": 1}),
            ("realizado", "realizado", "int64", {"h": 1}),
            ("atendido_no_lab", "Atendido no Lab", "boolean", {}),
        ],
    },
    "Atendimentos": {
        "csv": "fato_atendimentos",
        "desc": "Atendimentos do Worklab. UBS informada ou inferida pelo último agendamento (até 30 dias).",
        "cols": [
            ("atendimento_id", "atendimento_id", "int64", {"h": 1}),
            ("codigo_worklab", "Código Worklab", "string", {}),
            ("data", "data", "date", {"h": 1}),
            ("paciente_pseudo", "paciente_pseudo", "string", {"h": 1}),
            ("ubs_id", "ubs_id", "int64", {"h": 1}),
            ("origem_ubs", "Origem da UBS", "string", {}),
            ("convenio", "Convênio", "string", {}),
            ("tinha_agendamento", "Tinha Agendamento", "boolean", {}),
            ("qtd_exames", "qtd_exames", "int64", {"h": 1}),
            ("valor_total", "valor_total", "decimal", {"h": 1}),
        ],
    },
    "Exames Realizados": {
        "csv": "fato_exames",
        "desc": "Um exame por linha. TAT = liberação − (data do atendimento às 07:00).",
        "cols": [
            ("atendimento_exame_id", "atendimento_exame_id", "int64", {"h": 1}),
            ("atendimento_id", "atendimento_id", "int64", {"h": 1}),
            ("data", "data", "date", {"h": 1}),
            ("ubs_id", "ubs_id", "int64", {"h": 1}),
            ("exame_id", "exame_id", "int64", {"h": 1}),
            ("quantidade", "quantidade", "int64", {"h": 1}),
            ("valor", "valor", "decimal", {"h": 1}),
            ("situacao", "Situação", "string", {}),
            ("liberado_em", "Liberado em", "datetime", {"fmt": "dd/mm/yyyy hh:nn"}),
            ("tat_horas", "tat_horas", "double", {"h": 1}),
            ("dentro_prazo", "Dentro do Prazo", "boolean", {}),
        ],
    },
    "Não Conformidades": {
        "csv": "fato_nao_conformidades", "desc": "Não conformidades registradas no módulo de qualidade.",
        "cols": [
            ("nc_id", "nc_id", "int64", {"h": 1}),
            ("data", "data", "date", {"h": 1}),
            ("etapa", "Etapa", "string", {}),
            ("categoria", "Categoria", "string", {}),
            ("prioridade", "Prioridade", "string", {}),
            ("status", "Status", "string", {}),
            ("setor", "Setor", "string", {}),
            ("ubs_id", "ubs_id", "int64", {"h": 1}),
            ("prazo", "Prazo", "date", {"fmt": "dd/mm/yyyy"}),
            ("concluida_em", "Concluída em", "date", {"fmt": "dd/mm/yyyy"}),
            ("dias_resolucao", "dias_resolucao", "int64", {"h": 1}),
            ("vencida", "Vencida", "boolean", {}),
            ("qtd_acoes", "qtd_acoes", "int64", {"h": 1}),
            ("acoes_concluidas", "acoes_concluidas", "int64", {"h": 1}),
        ],
    },
    "Triagens": {
        "csv": "fato_triagens", "desc": "Triagens (sem identificação do paciente).",
        "cols": [
            ("triagem_id", "triagem_id", "int64", {"h": 1}),
            ("data", "data", "date", {"h": 1}),
            ("ubs_id", "ubs_id", "int64", {"h": 1}),
            ("prioridade", "Prioridade", "string", {}),
            ("situacao", "Situação", "string", {}),
            ("qtd_exames", "qtd_exames", "int64", {"h": 1}),
            ("horas_em_aberto", "horas_em_aberto", "double", {"h": 1}),
        ],
    },
}

TIPO_TMDL = {"int64": "int64", "string": "string", "decimal": "decimal", "double": "double",
             "date": "dateTime", "datetime": "dateTime", "time": "dateTime", "boolean": "boolean"}
TIPO_M = {"int64": "Int64.Type", "string": "type text", "decimal": "Currency.Type", "double": "type number",
          "date": "type date", "datetime": "type datetime", "time": "type time", "boolean": "type logical"}

RELACOES = [  # (tabela N, coluna, tabela 1, coluna)
    ("Agendamentos", "data", "Calendário", "Data"),
    ("Atendimentos", "data", "Calendário", "Data"),
    ("Exames Realizados", "data", "Calendário", "Data"),
    ("Não Conformidades", "data", "Calendário", "Data"),
    ("Triagens", "data", "Calendário", "Data"),
    ("Agendamentos", "ubs_id", "UBS", "ubs_id"),
    ("Atendimentos", "ubs_id", "UBS", "ubs_id"),
    ("Exames Realizados", "ubs_id", "UBS", "ubs_id"),
    ("Não Conformidades", "ubs_id", "UBS", "ubs_id"),
    ("Triagens", "ubs_id", "UBS", "ubs_id"),
    ("Exames Realizados", "exame_id", "Exame", "exame_id"),
]

PCT, INT, DEC1, PP, X = "0.0%", "#,##0", "#,##0.0", "+0.0;-0.0;0.0", '0.00"x"'

# (pasta, nome, DAX, formato, descrição)
MEDIDAS = [
    ("Agendamento", "Total de Agendamentos", "COUNTROWS ( Agendamentos )", INT, "Total de agendamentos (inclui pendentes)."),
    ("Agendamento", "Agendamentos Realizados", "SUM ( Agendamentos[realizado] )", INT,
     "Compareceram + faltaram. Base da taxa de faltas."),
    ("Agendamento", "Comparecimentos", "SUM ( Agendamentos[compareceu] )", INT, "Agendamentos com status COMPARECEU."),
    ("Agendamento", "Faltas", "SUM ( Agendamentos[faltou] )", INT, "Agendamentos com status FALTOU."),
    ("Agendamento", "Pendentes", 'CALCULATE ( [Total de Agendamentos], Agendamentos[Status] = "PENDENTE" )', INT,
     "Agendamentos ainda sem desfecho registrado."),
    ("Agendamento", "Taxa de Faltas", "DIVIDE ( [Faltas], [Agendamentos Realizados] )", PCT,
     "Faltas ÷ (compareceram + faltaram). Pendentes e agenda futura ficam fora."),
    ("Agendamento", "Taxa de Comparecimento", "DIVIDE ( [Comparecimentos], [Agendamentos Realizados] )", PCT,
     "Comparecimentos ÷ agendamentos realizados."),
    ("Agendamento", "Taxa de Faltas da Rede", "CALCULATE ( [Taxa de Faltas], REMOVEFILTERS ( UBS ) )", PCT,
     "Taxa de faltas de todas as UBS juntas, no período filtrado."),
    ("Agendamento", "Faltas vs Rede", "DIVIDE ( [Taxa de Faltas], [Taxa de Faltas da Rede] )", X,
     "Quantas vezes a taxa da UBS está acima (>1) ou abaixo (<1) da rede."),
    ("Agendamento", "Taxa de Faltas (mês anterior)",
     "IF (\n\tHASONEVALUE ( 'Calendário'[Ano-Mês] ),\n\tCALCULATE ( [Taxa de Faltas], DATEADD ( 'Calendário'[Data], -1, MONTH ) )\n)", PCT,
     "Taxa de faltas no mês anterior. Só aparece com um único mês selecionado."),
    ("Agendamento", "Δ Taxa de Faltas (p.p.)",
     "IF (\n\tNOT ISBLANK ( [Taxa de Faltas] ) && NOT ISBLANK ( [Taxa de Faltas (mês anterior)] ),\n"
     "\t( [Taxa de Faltas] - [Taxa de Faltas (mês anterior)] ) * 100\n)", PP,
     "Variação em pontos percentuais em relação ao mês anterior."),
    ("Agendamento", "Pacientes Agendados", "DISTINCTCOUNT ( Agendamentos[paciente_pseudo] )", INT,
     "Pacientes distintos (pseudônimo) com agendamento."),
    ("Cruzamento", "Comparecidos Atendidos no Lab %",
     "DIVIDE (\n\tCALCULATE ( [Comparecimentos], Agendamentos[Atendido no Lab] = TRUE () ),\n\t[Comparecimentos]\n)", PCT,
     "Dos que constam como 'compareceu', quantos têm atendimento no laboratório em até 7 dias."),
    ("Cruzamento", "Faltosos Atendidos no Lab %",
     "DIVIDE (\n\tCALCULATE ( [Faltas], Agendamentos[Atendido no Lab] = TRUE () ),\n\t[Faltas]\n)", PCT,
     "Dos que constam como 'faltou', quantos foram atendidos no laboratório em até 7 dias (status a revisar)."),
    ("Cruzamento", "Atendimentos sem Agendamento %",
     "DIVIDE (\n\tCALCULATE ( [Total de Atendimentos], Atendimentos[Tinha Agendamento] = FALSE () ),\n\t[Total de Atendimentos]\n)", PCT,
     "Atendimentos do laboratório sem agendamento do paciente nos 30 dias anteriores."),
    ("Produção", "Total de Atendimentos", "COUNTROWS ( Atendimentos )", INT, "Atendimentos no laboratório."),
    ("Produção", "Pacientes Atendidos", "DISTINCTCOUNT ( Atendimentos[paciente_pseudo] )", INT,
     "Pacientes distintos (pseudônimo) atendidos."),
    ("Produção", "Total de Exames", "SUM ( 'Exames Realizados'[quantidade] )", INT, "Exames realizados."),
    ("Produção", "Exames por Atendimento",
     "DIVIDE ( [Total de Exames], DISTINCTCOUNT ( 'Exames Realizados'[atendimento_id] ) )", DEC1,
     "Média de exames por atendimento (conta só atendimentos com exames no filtro)."),
    ("Produção", "Atendimentos por Dia", "AVERAGEX ( VALUES ( 'Calendário'[Data] ), [Total de Atendimentos] )", DEC1,
     "Média de atendimentos nos dias com atendimento."),
    ("Produção", "Exames (mês anterior)",
     "IF (\n\tHASONEVALUE ( 'Calendário'[Ano-Mês] ),\n\tCALCULATE ( [Total de Exames], DATEADD ( 'Calendário'[Data], -1, MONTH ) )\n)", INT,
     "Exames no mês anterior. Só aparece com um único mês selecionado."),
    ("Produção", "Variação de Exames %",
     "IF (\n\tHASONEVALUE ( 'Calendário'[Ano-Mês] ),\n\tDIVIDE ( [Total de Exames] - [Exames (mês anterior)], [Exames (mês anterior)] )\n)", "+0.0%;-0.0%;0.0%",
     "Variação percentual de exames em relação ao mês anterior."),
    ("Produção", "Valor de Referência (R$)", "SUM ( 'Exames Realizados'[valor] )", "R$ #,##0.00",
     "Soma do valor de referência dos exames (ilustrativo)."),
    ("Prazo", "TAT Médio (h)", "AVERAGE ( 'Exames Realizados'[tat_horas] )", DEC1,
     "Tempo médio de liberação em horas."),
    ("Prazo", "% Liberado no Prazo",
     "DIVIDE (\n\tCALCULATE ( COUNTROWS ( 'Exames Realizados' ), 'Exames Realizados'[Dentro do Prazo] = TRUE () ),\n"
     "\tCALCULATE ( COUNTROWS ( 'Exames Realizados' ), NOT ISBLANK ( 'Exames Realizados'[Dentro do Prazo] ) )\n)", PCT,
     "Exames liberados dentro do prazo do catálogo ÷ exames liberados."),
    ("Prazo", "Taxa de Recoleta",
     "DIVIDE (\n\tCALCULATE ( COUNTROWS ( 'Exames Realizados' ), 'Exames Realizados'[Situação] = \"RECOLETA\" ),\n"
     "\tCOUNTROWS ( 'Exames Realizados' )\n)", PCT, "Exames com situação RECOLETA ÷ exames."),
    ("Qualidade", "NCs", "COUNTROWS ( 'Não Conformidades' )", INT, "Não conformidades registradas."),
    ("Qualidade", "NCs Abertas",
     "CALCULATE ( [NCs], 'Não Conformidades'[Status] IN { \"ABERTA\", \"EM_TRATAMENTO\" } )", INT,
     "Não conformidades abertas ou em tratamento."),
    ("Qualidade", "NCs Vencidas", "CALCULATE ( [NCs], 'Não Conformidades'[Vencida] = TRUE () )", INT,
     "Abertas com prazo vencido."),
    ("Qualidade", "Tempo Médio de Resolução (dias)", "AVERAGE ( 'Não Conformidades'[dias_resolucao] )", DEC1,
     "Dias entre o registro e a conclusão."),
    ("Qualidade", "% Ações 5W2H Concluídas",
     "DIVIDE ( SUM ( 'Não Conformidades'[acoes_concluidas] ), SUM ( 'Não Conformidades'[qtd_acoes] ) )", PCT,
     "Ações 5W2H concluídas ÷ ações cadastradas."),
    ("Triagem", "Total de Triagens", "COUNTROWS ( Triagens )", INT, "Triagens registradas."),
    ("Triagem", "Triagens Aguardando", 'CALCULATE ( [Total de Triagens], Triagens[Situação] = "AGUARDANDO" )', INT,
     "Triagens ainda não confirmadas."),
]


def tmdl_expr(texto: str, nivel: int) -> str:
    """Bloco multi-linha entre ``` com indentação TMDL."""
    ind = T * nivel
    linhas = texto.strip("\n").split("\n")
    return "```\n" + "\n".join(ind + ln for ln in linhas) + "\n" + ind + "```"


def m_tabela(nome: str, spec: dict) -> str:
    cols = spec["cols"]
    tipos = ", ".join(f'{{"{c}", {TIPO_M[t]}}}' for c, _, t, _ in cols)
    nomes = ", ".join(f'"{c}"' for c, _, _, _ in cols)
    ren = ", ".join(f'{{"{c}", "{n}"}}' for c, n, _, _ in cols if c != n)
    m = f'''let
    Fonte = Csv.Document(File.Contents(Text.TrimEnd(PastaDados, "\\") & "\\{spec["csv"]}.csv"), [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Cabecalhos = Table.PromoteHeaders(Fonte, [PromoteAllScalars = true]),
    Colunas = Table.SelectColumns(Cabecalhos, {{{nomes}}}, MissingField.UseNull),
    Vazios = Table.TransformColumns(Colunas, {{}}, each if _ = "" then null else _),
    Tipos = Table.TransformColumnTypes(Vazios, {{{tipos}}}, "en-US")'''
    if nome == "UBS":
        m += """,
    SemUBS = Table.Combine({Tipos, #table(type table [ubs_id = Int64.Type, ubs = text, tipo = text, ativo = logical],
        {{0, "Não identificada", "Sem UBS", true}})})"""
        ult = "SemUBS"
    elif "ubs_id" in [c for c, _, _, _ in cols]:
        m += """,
    SemUBS = Table.ReplaceValue(Tipos, null, 0, Replacer.ReplaceValue, {"ubs_id"})"""
        ult = "SemUBS"
    else:
        ult = "Tipos"
    if ren:
        m += f''',
    Nomes = Table.RenameColumns({ult}, {{{ren}}})
in
    Nomes'''
    else:
        m += f"\nin\n    {ult}"
    return m


def gerar_modelo(destino: pathlib.Path, pasta_dados: str) -> None:
    d = destino / "definition"
    (d / "tables").mkdir(parents=True)
    (destino / "definition.pbism").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {"qnaEnabled": False}}, indent=2), encoding="utf-8")
    (destino / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NOME},
        "config": {"version": "2.0", "logicalId": guid("semanticModel")}}, indent=2), encoding="utf-8")

    (d / "database.tmdl").write_text(
        f"database {guid('database')}\n{T}compatibilityLevel: 1601\n", encoding="utf-8")

    refs = "\n".join(f"ref table {q(n)}" for n in [*TABELAS, "Medidas"])
    (d / "model.tmdl").write_text(
        "model Model\n"
        f"{T}culture: pt-BR\n"
        f"{T}defaultPowerBIDataSourceVersion: powerBI_V3\n"
        f"{T}discourageImplicitMeasures\n"
        f"{T}sourceQueryCulture: pt-BR\n"
        f"{T}dataAccessOptions\n{T}{T}legacyRedirects\n{T}{T}returnErrorValuesAsNull\n\n"
        f"annotation __PBI_TimeIntelligenceEnabled = 0\n\n"
        f"{refs}\n\nref cultureInfo pt-BR\n", encoding="utf-8")

    (d / "cultures").mkdir()
    (d / "cultures" / "pt-BR.tmdl").write_text("cultureInfo pt-BR\n", encoding="utf-8")

    caminho = pasta_dados.replace('"', '""')
    (d / "expressions.tmdl").write_text(
        "/// Pasta com os CSV gerados por: python -m scripts.exportar_powerbi\n"
        f'expression PastaDados = "{caminho}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n',
        encoding="utf-8")

    for nome, spec in TABELAS.items():
        out = [f"/// {spec['desc']}", f"table {q(nome)}"]
        if spec.get("data"):
            out.append(f"{T}dataCategory: Time")
        for csv, col, tipo, op in spec["cols"]:
            out.append("")
            out.append(f"{T}column {q(col)}")
            out.append(f"{T}{T}dataType: {TIPO_TMDL[tipo]}")
            if op.get("k"):
                out.append(f"{T}{T}isKey")
            if op.get("h"):
                out.append(f"{T}{T}isHidden")
            fmt = op.get("fmt")
            if not fmt and tipo == "date":
                fmt = "dd/mm/yyyy"
            if fmt:
                out.append(f"{T}{T}formatString: {fmt}")
            out.append(f"{T}{T}summarizeBy: none")
            out.append(f"{T}{T}sourceColumn: {col}")
            if op.get("sort"):
                out.append(f"{T}{T}sortByColumn: {q(op['sort'])}")
        out.append("")
        out.append(f"{T}partition {q(nome)} = m")
        out.append(f"{T}{T}mode: import")
        out.append(f"{T}{T}source = " + tmdl_expr(m_tabela(nome, spec), 3))
        (d / "tables" / f"{nome}.tmdl").write_text("\n".join(out) + "\n", encoding="utf-8")

    # tabela só de medidas
    out = ["/// Indicadores do LACEN Intelligence (mesmas definições do site).", "table Medidas"]
    for pasta, nome, dax, fmt, desc in MEDIDAS:
        out.append("")
        out.append(f"{T}/// {desc}")
        expr = dax if "\n" not in dax else tmdl_expr(dax, 2)
        out.append(f"{T}measure {q(nome)} = {expr}")
        out.append(f"{T}{T}formatString: {fmt}")
        out.append(f"{T}{T}displayFolder: {pasta}")
    out += ["", f"{T}column Ordem", f"{T}{T}dataType: int64", f"{T}{T}isHidden", f"{T}{T}formatString: 0",
            f"{T}{T}summarizeBy: none", f"{T}{T}sourceColumn: [Ordem]", "",
            f"{T}partition Medidas = calculated", f"{T}{T}mode: import", f'{T}{T}source = ROW ( "Ordem", 0 )']
    (d / "tables" / "Medidas.tmdl").write_text("\n".join(out) + "\n", encoding="utf-8")

    rel = []
    for tn, cn, t1, c1 in RELACOES:
        rel.append(f"relationship {guid('rel', tn, cn, t1, c1)}")
        rel.append(f"{T}fromColumn: {q(tn)}.{q(cn)}")
        rel.append(f"{T}toColumn: {q(t1)}.{q(c1)}")
        rel.append("")
    (d / "relationships.tmdl").write_text("\n".join(rel), encoding="utf-8")


# =============================================================================
# RELATÓRIO (PBIR)
# =============================================================================
def lit(v) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def col(tabela: str, coluna: str) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": tabela}}, "Property": coluna}},
            "queryRef": f"{tabela}.{coluna}", "nativeQueryRef": coluna}


def med(nome: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": "Medidas"}}, "Property": nome}},
            "queryRef": f"Medidas.{nome}", "nativeQueryRef": nome}


def titulo_vco(texto: str | None) -> dict:
    if not texto:
        return {"title": [{"properties": {"show": lit(False)}}]}
    return {"title": [{"properties": {"show": lit(True), "text": lit(texto)}}]}


class Pagina:
    def __init__(self, nome: str, titulo: str, subtitulo: str):
        self.id = hexid("page", nome)
        self.nome = nome
        self.titulo = titulo
        self.visuais: list[dict] = []
        self.z = 0
        self.texto(titulo, 16, 2, 760, 50, 20, bold=True)
        self.texto(subtitulo, 16, 52, 860, 34, 10, cor="#5B6770")
        self.slicer("Calendário", "Ano-Mês", 900, 6, 180, 78)
        self.slicer("UBS", "UBS", 1090, 6, 174, 78)

    def _add(self, tipo: str, chave: str, x, y, w, h, visual: dict) -> None:
        self.z += 1000
        v = {"$schema": VC, "name": hexid(self.nome, chave),
             "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
             "visual": {"visualType": tipo, **visual}}
        self.visuais.append(v)

    def texto(self, texto, x, y, w, h, tamanho, bold=False, cor="#1F2A30"):
        run = {"value": texto, "textStyle": {"fontFamily": "Segoe UI Semibold" if bold else "Segoe UI",
                                              "fontSize": f"{tamanho}pt", "color": cor}}
        self._add("textbox", "txt" + texto, x, y, w, h, {
            "objects": {"general": [{"properties": {"paragraphs": [
                {"textRuns": [run], "horizontalTextAlignment": "left"}]}}]},
            "visualContainerObjects": {"background": [{"properties": {"show": lit(False)}}],
                                       "border": [{"properties": {"show": lit(False)}}]},
            "drillFilterOtherVisuals": True})

    def slicer(self, tabela, coluna, x, y, w, h):
        self._add("slicer", "sl" + coluna, x, y, w, h, {
            "query": {"queryState": {"Values": {"projections": [{**col(tabela, coluna), "active": True}]}}},
            "objects": {"data": [{"properties": {"mode": lit("Dropdown")}}]},
            "syncGroup": {"groupName": f"sync_{coluna}", "fieldChanges": True, "filterChanges": True},
            "drillFilterOtherVisuals": True})

    @staticmethod
    def _corpo(y, h):
        """Área de conteúdo: o layout é escrito para y 76–704 e reposicionado abaixo do cabeçalho (96–708)."""
        k = (708 - 96) / (704 - 76)
        return round(96 + (y - 76) * k), round(h * k)

    def cartoes(self, medidas, x, y, w, h, chave="cards"):
        y, h = self._corpo(y, h)
        self._add("cardVisual", chave, x, y, w, h, {
            "query": {"queryState": {"Data": {"projections": [med(m) for m in medidas]}}},
            "objects": {"layout": [{"properties": {"style": lit("Cards")}, "selector": {"id": "default"}}]},
            "visualContainerObjects": titulo_vco(None),
            "drillFilterOtherVisuals": True})

    def grafico(self, tipo, titulo, x, y, w, h, papeis: dict, ordenar=None, chave=None):
        y, h = self._corpo(y, h)
        query = {"queryState": {p: {"projections": proj} for p, proj in papeis.items()}}
        if ordenar:
            campo, direcao = ordenar
            query["sortDefinition"] = {"sort": [{"field": campo["field"], "direction": direcao}],
                                       "isDefaultSort": False}
        self._add(tipo, chave or titulo, x, y, w, h, {
            "query": query, "visualContainerObjects": titulo_vco(titulo), "drillFilterOtherVisuals": True})


def paginas() -> list[Pagina]:
    cal, ubs = ("Calendário", "Ano-Mês"), ("UBS", "UBS")
    ps = []

    p = Pagina("executiva", "Visão Executiva",
               "Taxa de faltas = faltas ÷ (compareceram + faltaram). Pendentes e agenda futura ficam fora.")
    p.cartoes(["Total de Agendamentos", "Taxa de Faltas", "Total de Atendimentos", "Total de Exames", "Pacientes Atendidos",
               "Atendimentos sem Agendamento %"], 16, 76, 1248, 108)
    p.grafico("lineClusteredColumnComboChart", "Atendimentos (colunas) e taxa de faltas (linha) por mês",
              16, 196, 620, 254, {"Category": [col(*cal)], "Y": [med("Total de Atendimentos")], "Y2": [med("Taxa de Faltas")]},
              ordenar=(col(*cal), "Ascending"))
    p.grafico("clusteredBarChart", "Taxa de faltas por UBS", 648, 196, 616, 508,
              {"Category": [col(*ubs)], "Y": [med("Taxa de Faltas")], "Tooltips": [med("Agendamentos Realizados")]},
              ordenar=(med("Taxa de Faltas"), "Descending"))
    p.grafico("columnChart", "Atendimentos por dia", 16, 462, 620, 242,
              {"Category": [col("Calendário", "Data")], "Y": [med("Total de Atendimentos")]},
              ordenar=(col("Calendário", "Data"), "Ascending"))
    ps.append(p)

    p = Pagina("ubs", "UBS", "Onde a falta pesa mais e como cada unidade se compara com a rede.")
    p.grafico("clusteredBarChart", "Taxa de faltas por UBS", 16, 76, 520, 628,
              {"Category": [col(*ubs)], "Y": [med("Taxa de Faltas")],
               "Tooltips": [med("Faltas vs Rede"), med("Agendamentos Realizados")]},
              ordenar=(med("Taxa de Faltas"), "Descending"))
    p.grafico("tableEx", "Resumo por UBS", 548, 76, 716, 300,
              {"Values": [col(*ubs), med("Total de Agendamentos"), med("Faltas"), med("Taxa de Faltas"),
                          med("Δ Taxa de Faltas (p.p.)"), med("Faltas vs Rede"), med("Total de Atendimentos")]},
              ordenar=(med("Taxa de Faltas"), "Descending"))
    p.grafico("pivotTable", "Taxa de faltas por UBS e dia da semana", 548, 388, 716, 316,
              {"Rows": [col(*ubs)], "Columns": [col("Calendário", "Dia da Semana")], "Values": [med("Taxa de Faltas")]})
    ps.append(p)

    p = Pagina("exames", "Exames", "O que o laboratório produz e o que mudou em relação ao mês anterior.")
    p.grafico("clusteredBarChart", "Exames por sigla", 16, 76, 400, 628,
              {"Category": [col("Exame", "Sigla")], "Y": [med("Total de Exames")], "Tooltips": [med("Variação de Exames %")]},
              ordenar=(med("Total de Exames"), "Descending"))
    p.grafico("treemap", "Exames por setor", 428, 76, 400, 300,
              {"Group": [col("Exame", "Setor")], "Values": [med("Total de Exames")]})
    p.grafico("lineChart", "Exames por mês", 840, 76, 424, 300,
              {"Category": [col(*cal)], "Y": [med("Total de Exames")]}, ordenar=(col(*cal), "Ascending"))
    p.grafico("tableEx", "Exames: volume e variação", 428, 388, 836, 316,
              {"Values": [col("Exame", "Sigla"), col("Exame", "Exame"), col("Exame", "Setor"), med("Total de Exames"),
                          med("Exames (mês anterior)"), med("Variação de Exames %")]},
              ordenar=(med("Total de Exames"), "Descending"))
    ps.append(p)

    p = Pagina("gestao", "Gestão", "Gargalos: prazo de liberação, recoleta e a ligação entre agenda e laboratório.")
    p.cartoes(["Atendimentos por Dia", "Exames por Atendimento", "Atendimentos sem Agendamento %",
               "Comparecidos Atendidos no Lab %", "Faltosos Atendidos no Lab %", "Pendentes"], 16, 76, 1248, 108)
    p.grafico("clusteredBarChart", "Tempo médio de liberação (h) por setor", 16, 196, 400, 508,
              {"Category": [col("Exame", "Setor")], "Y": [med("TAT Médio (h)")],
               "Tooltips": [med("% Liberado no Prazo")]}, ordenar=(med("TAT Médio (h)"), "Descending"))
    p.grafico("lineChart", "% liberado no prazo por mês", 428, 196, 420, 254,
              {"Category": [col(*cal)], "Y": [med("% Liberado no Prazo")]}, ordenar=(col(*cal), "Ascending"))
    p.grafico("clusteredColumnChart", "Taxa de faltas por dia da semana", 860, 196, 404, 254,
              {"Category": [col("Calendário", "Dia da Semana")], "Y": [med("Taxa de Faltas")]},
              ordenar=(col("Calendário", "Dia da Semana"), "Ascending"))
    p.grafico("clusteredColumnChart", "Taxa de recoleta por UBS", 428, 462, 836, 242,
              {"Category": [col(*ubs)], "Y": [med("Taxa de Recoleta")]}, ordenar=(med("Taxa de Recoleta"), "Descending"))
    ps.append(p)

    p = Pagina("qualidade", "Qualidade", "Não conformidades e planos de ação 5W2H.")
    p.cartoes(["NCs", "NCs Abertas", "NCs Vencidas", "Tempo Médio de Resolução (dias)", "% Ações 5W2H Concluídas"],
              16, 76, 1248, 108)
    p.grafico("columnChart", "Não conformidades por mês e etapa", 16, 196, 620, 254,
              {"Category": [col(*cal)], "Series": [col("Não Conformidades", "Etapa")], "Y": [med("NCs")]},
              ordenar=(col(*cal), "Ascending"))
    p.grafico("clusteredBarChart", "Não conformidades por categoria", 648, 196, 616, 254,
              {"Category": [col("Não Conformidades", "Categoria")], "Y": [med("NCs")]},
              ordenar=(med("NCs"), "Descending"))
    nc = "Não Conformidades"
    p.grafico("tableEx", "Não conformidades: situação e prazo", 16, 462, 1248, 242,
              {"Values": [col(nc, "Etapa"), col(nc, "Categoria"), col(nc, "Prioridade"), col(nc, "Status"),
                          col(nc, "Setor"), col(nc, "Prazo"), col(nc, "Vencida"), med("NCs")]},
              ordenar=(col(nc, "Prazo"), "Ascending"))
    ps.append(p)
    return ps


TEMA = {
    "name": "LACEN_Intelligence.json",  # o validador exige o mesmo nome referenciado no report.json
    "dataColors": ["#0F766E", "#F59E0B", "#2563EB", "#DC2626", "#7C3AED", "#0891B2", "#65A30D", "#DB2777",
                   "#475569", "#EA580C"],
    "foreground": "#1F2A30", "background": "#FFFFFF", "tableAccent": "#0F766E",
    "good": "#15803D", "neutral": "#F59E0B", "bad": "#DC2626",
    "maximum": "#0F766E", "center": "#FDE68A", "minimum": "#FFFFFF",
    "textClasses": {
        "callout": {"fontSize": 22, "fontFace": "Segoe UI Semibold", "color": "#1F2A30"},
        "title": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": "#1F2A30"},
        "header": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": "#1F2A30"},
        "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": "#5B6770"},
    },
    "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": "#F4F6F7"}}, "transparency": 0}]}}},
}


def gerar_relatorio(destino: pathlib.Path, modelo_scaffold: pathlib.Path) -> None:
    """Parte do esqueleto gerado pela CLI oficial (powerbi-report-author scaffold) e adiciona páginas/visuais."""
    shutil.copytree(modelo_scaffold, destino)
    d = destino / "definition"
    for pg in (d / "pages").iterdir():
        if pg.is_dir():
            shutil.rmtree(pg)
    rel = json.loads((d / "report.json").read_text(encoding="utf-8"))
    base = rel["themeCollection"]["baseTheme"]
    rel["themeCollection"]["customTheme"] = {"name": "LACEN_Intelligence.json",
                                             "reportVersionAtImport": base["reportVersionAtImport"],
                                             "type": "RegisteredResources"}
    rel["resourcePackages"] = [r for r in rel["resourcePackages"] if r["name"] != "RegisteredResources"] + [
        {"name": "RegisteredResources", "type": "RegisteredResources",
         "items": [{"name": "LACEN_Intelligence.json", "path": "LACEN_Intelligence.json", "type": "CustomTheme"}]}]
    (d / "report.json").write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
    rr = destino / "StaticResources" / "RegisteredResources"
    rr.mkdir(parents=True, exist_ok=True)
    (rr / "LACEN_Intelligence.json").write_text(json.dumps(TEMA, ensure_ascii=False, indent=2), encoding="utf-8")

    ps = paginas()
    page_schema = json.loads(next((modelo_scaffold / "definition" / "pages").glob("*/page.json")).read_text())["$schema"]
    pm = json.loads((d / "pages" / "pages.json").read_text(encoding="utf-8"))
    pm["pageOrder"] = [p.id for p in ps]
    pm["activePageName"] = ps[0].id
    (d / "pages" / "pages.json").write_text(json.dumps(pm, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in ps:
        pd_ = d / "pages" / p.id
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps({
            "$schema": page_schema, "name": p.id, "displayName": p.titulo,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, ensure_ascii=False, indent=2), encoding="utf-8")
        for v in p.visuais:
            vd = pd_ / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", default=str((RAIZ / "data" / "powerbi").resolve()),
                    help="pasta dos CSV (padrão: data/powerbi deste projeto)")
    ap.add_argument("--saida", default=str(SAIDA))
    a = ap.parse_args()
    saida = pathlib.Path(a.saida)
    esqueleto = pathlib.Path(__file__).resolve().parent / "esqueleto" / RP
    for alvo in (saida / SM, saida / RP):
        if alvo.exists():
            shutil.rmtree(alvo)
    gerar_modelo(saida / SM, a.pasta)
    gerar_relatorio(saida / RP, esqueleto)
    (saida / f"{NOME}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": RP}}],
        "settings": {"enableAutoRecovery": True}}, indent=2), encoding="utf-8")
    print(f"✓ Projeto gerado em {saida / (NOME + '.pbip')}")
    print(f"  Fonte dos dados: {a.pasta}")


if __name__ == "__main__":
    main()
