"""Pipeline de importação: Arquivo → pandas → validação → padronização → anonimização → PostgreSQL.

Fontes suportadas:
  AGENDAMENTOS  planilhas/CSV de agendamento das UBS (uma linha por paciente agendado)
  WORKLAB       export do Worklab (uma linha por exame realizado)

Tudo o que é rejeitado fica registrado em importacao_erros, com a linha e o motivo,
e dados pessoais nunca saem desta etapa: nome/CPF/CNS viram pseudo_id (PAC_…) e
a coluna de observações em texto livre é descartada.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .anonimizacao import ESTRATEGIA_PADRAO, chave_paciente, pseudo_id
from .normalizacao import (mapear_valor, mascarar, normalizar_cabecalho, normalizar_texto,
                           parse_data, parse_hora)

# ----------------------------------------------------------------------------
# Dicionários de padronização
# ----------------------------------------------------------------------------
COLUNAS = {
    "AGENDAMENTOS": {
        "data": ["data", "data_agendamento", "dt_agendamento", "data_do_agendamento", "data_agendada", "data_marcada",
                 "data_hora", "data_e_hora", "data_hora_agendamento", "dia", "data_consulta", "data_do_exame",
                 "data_coleta", "data_da_coleta"],
        "horario": ["horario", "hora", "horario_agendamento", "hora_agendamento", "hora_marcada", "horario_marcado"],
        "ubs": ["ubs", "unidade", "unidade_de_saude", "estabelecimento", "posto", "unidade_solicitante",
                "estabelecimento_de_saude", "unidade_basica", "unidade_basica_de_saude", "local", "origem"],
        "nome": ["paciente", "nome", "nome_paciente", "nome_do_paciente", "usuario", "nome_do_usuario", "cidadao",
                 "nome_completo"],
        "cpf": ["cpf", "cpf_paciente", "cpf_do_paciente", "n_cpf", "no_cpf"],
        "cns": ["cns", "cartao_sus", "cartao_nacional_de_saude", "n_cartao_sus", "no_cartao_sus", "cns_paciente",
                "cartao_do_sus", "cartao_sus_cns"],
        "nascimento": ["data_nascimento", "nascimento", "dt_nascimento", "data_de_nascimento", "dt_nasc", "data_nasc",
                       "nasc"],
        "codigo": ["id_paciente", "codigo_paciente", "cod_paciente", "prontuario", "n_prontuario", "no_prontuario"],
        "tipo": ["tipo", "tipo_agendamento", "tipo_de_atendimento", "tipo_de_agendamento", "modalidade"],
        "status": ["status", "situacao", "comparecimento", "status_agendamento", "situacao_agendamento",
                   "presenca", "compareceu", "status_do_agendamento", "situacao_do_agendamento"],
        "observacoes": ["observacoes", "observacao", "obs"],
    },
    "WORKLAB": {
        "atendimento": ["atendimento", "id_atendimento", "codigo_atendimento", "n_atendimento", "no_atendimento",
                        "num_atendimento", "numero_atendimento", "numero_do_atendimento", "no_do_atendimento",
                        "cod_atendimento", "requisicao", "n_requisicao", "no_requisicao", "os", "n_os", "no_os",
                        "ordem_de_servico", "pedido", "n_pedido", "no_pedido", "protocolo", "amostra"],
        "data": ["data", "data_atendimento", "dt_atendimento", "data_coleta", "data_da_coleta", "data_do_atendimento",
                 "data_cadastro", "data_de_cadastro", "data_entrada", "data_de_entrada", "data_hora_atendimento",
                 "data_hora_coleta", "dt_coleta"],
        "nome": ["paciente", "nome", "nome_paciente", "nome_do_paciente", "nome_completo"],
        "cpf": ["cpf", "cpf_paciente", "cpf_do_paciente"],
        "cns": ["cns", "cartao_sus", "cartao_nacional_de_saude", "n_cartao_sus", "no_cartao_sus", "cns_paciente"],
        "nascimento": ["data_nascimento", "nascimento", "dt_nascimento", "data_de_nascimento", "dt_nasc", "data_nasc"],
        "codigo": ["id_paciente", "codigo_paciente", "cod_paciente", "prontuario", "n_prontuario", "no_prontuario"],
        "ubs": ["ubs", "unidade", "origem", "unidade_solicitante", "posto", "posto_de_coleta", "local_de_coleta",
                "unidade_de_origem", "estabelecimento", "local", "procedencia"],
        "convenio": ["convenio"],
        "exame": ["sigla", "sigla_exame", "sigla_do_exame", "mnemonico", "cod_exame", "codigo_exame", "exame",
                  "codigo_do_exame"],
        "nome_exame": ["nome_exame", "nome_do_exame", "descricao_exame", "descricao_do_exame", "exame_descricao",
                       "descricao", "procedimento", "exame"],
        "quantidade": ["quantidade", "qtd", "qtde"],
        "valor": ["valor", "valor_exame", "preco", "valor_r", "valor_rs", "valor_do_exame", "valor_unitario"],
        "situacao": ["situacao", "status", "status_exame", "situacao_exame", "situacao_do_exame"],
        "liberado_em": ["liberado_em", "data_liberacao", "dt_liberacao", "liberacao", "data_de_liberacao",
                        "data_hora_liberacao", "liberado"],
    },
}
# Quando o nome exato não está na lista: palavras que o cabeçalho deve conter (e não conter).
PALAVRAS = {
    "AGENDAMENTOS": {
        "data": (("data", "dt_", "dia"), ("nasc",)),
        "ubs": (("unidade", "ubs", "estabelecimento", "posto"), ("profissional",)),
        "status": (("status", "situacao", "compareci", "presenca"), ()),
        "nome": (("paciente", "nome"), ("mae", "pai", "profissional", "medico", "solicitante", "social", "unidade")),
        "nascimento": (("nasc",), ()),
        "cpf": (("cpf",), ()),
        "cns": (("cns", "cartao"), ()),
        "horario": (("hora",), ()),
    },
    "WORKLAB": {
        "atendimento": (("atendimento", "requisicao", "pedido", "protocolo", "ordem"), ("data", "hora", "local", "tipo")),
        "data": (("data", "dt_"), ("nasc", "libera", "entrega", "resultado")),
        "exame": (("sigla", "mnemonico", "exame"), ("data", "valor", "situacao", "status", "qtd", "quantidade")),
        "nome": (("paciente", "nome"), ("mae", "pai", "medico", "solicitante", "exame", "social", "unidade")),
        "nascimento": (("nasc",), ()),
        "cpf": (("cpf",), ()),
        "cns": (("cns", "cartao"), ()),
        "ubs": (("unidade", "ubs", "posto", "origem", "procedencia"), ()),
        "liberado_em": (("libera",), ()),
        "valor": (("valor",), ()),
    },
}
OBRIGATORIAS = {"AGENDAMENTOS": ["data", "ubs", "status"], "WORKLAB": ["atendimento", "data", "exame"]}
ROTULO_CAMPO = {"data": "data", "horario": "horário", "ubs": "UBS", "tipo": "tipo", "status": "status",
                "observacoes": "observações (descartada)", "atendimento": "atendimento", "exame": "exame",
                "nome_exame": "nome do exame", "quantidade": "quantidade", "valor": "valor", "situacao": "situação",
                "liberado_em": "liberação", "convenio": "convênio"}
DESCRICAO_CAMPO = {"data": "data", "ubs": "UBS/unidade", "status": "status (compareceu/faltou)",
                   "atendimento": "número do atendimento", "exame": "exame/sigla"}

STATUS_AGEND = {"COMPARECEU": "COMPARECEU", "PRESENTE": "COMPARECEU", "ATENDIDO": "COMPARECEU",
                "REALIZADO": "COMPARECEU", "SIM": "COMPARECEU",
                "FALTOU": "FALTOU", "FALTA": "FALTOU", "AUSENTE": "FALTOU", "NAO COMPARECEU": "FALTOU",
                "NAO": "FALTOU",
                "PENDENTE": "PENDENTE", "AGUARDANDO": "PENDENTE",
                "AGENDADO": "AGENDADO", "MARCADO": "AGENDADO", "CONFIRMADO": "AGENDADO",
                "COMPARECIDO": "COMPARECEU", "PRESENCA": "COMPARECEU", "COLETADO": "COMPARECEU",
                "CONCLUIDO": "COMPARECEU", "FINALIZADO": "COMPARECEU", "EXECUTADO": "COMPARECEU",
                "NAO COMPARECIMENTO": "FALTOU", "NAO REALIZADO": "FALTOU", "AUSENCIA": "FALTOU", "FALTOSO": "FALTOU",
                "NAO COMPARECEU": "FALTOU", "N": "FALTOU", "S": "COMPARECEU",
                "CANCELADO": "IGNORAR", "CANCELADA": "IGNORAR", "DESMARCADO": "IGNORAR", "DESMARCADA": "IGNORAR",
                "REMARCADO": "IGNORAR", "REMARCADA": "IGNORAR", "REAGENDADO": "IGNORAR", "EXCLUIDO": "IGNORAR"}
TIPO_AGEND = {"EXAME DE ROTINA": "ROTINA", "ROTINA": "ROTINA", "ELETIVO": "ROTINA",
              "URGENCIA": "URGENCIA", "URGENTE": "URGENCIA", "PRIORITARIO": "URGENCIA"}
SITUACAO_EXAME = {"LIBERADO": "LIBERADO", "CONCLUIDO": "LIBERADO", "FINALIZADO": "LIBERADO",
                  "COLETADO": "COLETADO", "EM ANALISE": "EM_ANALISE", "EM ANDAMENTO": "EM_ANALISE",
                  "RECOLETA": "RECOLETA", "NOVA COLETA": "RECOLETA",
                  "CANCELADO": "CANCELADO"}
CAMPOS_PESSOAIS = {"nome", "cpf", "cns", "nascimento", "codigo"}


class ArquivoJaImportado(Exception):
    def __init__(self, importacao_id: int):
        super().__init__(f"Este arquivo já foi importado (importação #{importacao_id}).")
        self.importacao_id = importacao_id


class ColunasAusentes(Exception):
    pass


class ArquivoIlegivel(Exception):
    """O arquivo não pôde ser lido como planilha (mensagem pronta para mostrar ao usuário)."""


@dataclass
class Relatorio:
    fonte: str
    arquivo: str
    hash_arquivo: str
    linhas_lidas: int = 0
    inseridas: int = 0
    duplicadas: int = 0
    rejeitadas: int = 0
    periodo_inicio: date | None = None
    periodo_fim: date | None = None
    colunas_reconhecidas: dict = field(default_factory=dict)
    erros: list = field(default_factory=list)
    importacao_id: int | None = None
    status: str = "PROCESSANDO"

    def registrar(self, linha, campo, valor, problema, severidade="REJEITADA", info=False):
        """info=True: nota informativa (ex.: colunas usadas) — aparece no relatório mas não é alerta."""
        if campo in CAMPOS_PESSOAIS:
            valor = mascarar(valor)
        self.erros.append({"linha": int(linha) if linha is not None else None, "campo": campo,
                           "valor": None if valor is None else str(valor)[:120],
                           "problema": problema, "severidade": severidade, "info": info})

    @property
    def avisos(self) -> int:
        return sum(1 for e in self.erros if e["severidade"] == "AVISO" and not e.get("info"))

    @property
    def tem_alertas(self) -> bool:
        return any(not e.get("info") for e in self.erros)

    def resumo(self) -> dict:
        return {k: getattr(self, k) for k in ("importacao_id", "fonte", "arquivo", "linhas_lidas", "inseridas",
                                               "duplicadas", "rejeitadas", "avisos", "periodo_inicio",
                                               "periodo_fim", "status")}


# ----------------------------------------------------------------------------
# Leitura
# ----------------------------------------------------------------------------
def _decodificar(conteudo: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _pontuar_cabecalho(valores, fonte: str | None) -> int:
    """Quantas células desta linha parecem nomes de coluna conhecidos."""
    conhecidos = set()
    for f in ([fonte] if fonte else list(COLUNAS)):
        for sin in COLUNAS[f].values():
            conhecidos.update(sin)
    pts = 0
    for v in valores:
        n = normalizar_cabecalho(v)
        if n and n in conhecidos:
            pts += 2
        elif n and any(k in n for k in ("data", "paciente", "unidade", "exame", "status", "situacao", "atendimento")):
            pts += 1
    return pts


def _com_cabecalho(grade: pd.DataFrame, fonte: str | None) -> tuple[pd.DataFrame, int]:
    """Acha a linha de cabeçalho nas primeiras 30 (relatórios costumam ter título/filtros em cima)."""
    grade = grade.dropna(how="all").dropna(axis=1, how="all")
    if grade.empty:
        return grade, 0
    linhas = grade.head(30).astype(object).values.tolist()
    notas = [_pontuar_cabecalho(l, fonte) for l in linhas]
    i = max(range(len(notas)), key=lambda k: (notas[k], -k))
    cab = [str(c).strip() if not (isinstance(c, float) and pd.isna(c)) and str(c).strip() else f"coluna_{j + 1}"
           for j, c in enumerate(linhas[i])]
    vistos: dict[str, int] = {}
    for j, c in enumerate(cab):                      # nomes repetidos: "Data", "Data_2"
        vistos[c] = vistos.get(c, 0) + 1
        if vistos[c] > 1:
            cab[j] = f"{c}_{vistos[c]}"
    pos = grade.index.get_loc(grade.index[i])
    corpo = grade.iloc[pos + 1:].copy()
    corpo.columns = cab
    corpo = corpo.dropna(how="all")
    corpo.attrs["linha_excel"] = True          # índice = posição na planilha (0 = linha 1)
    return corpo, notas[i]


def _grades_do_arquivo(conteudo: bytes, nome: str) -> list[pd.DataFrame]:
    """Todas as tabelas candidatas do arquivo, sem cabeçalho (cada célula como veio)."""
    inicio = conteudo[:2048].lstrip().lower()
    try:
        if conteudo[:2] == b"PK":                                   # .xlsx / .xlsm
            return list(pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, dtype=object,
                                      engine="openpyxl").values())
        if conteudo[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # .xls (Excel 97-2003)
            return list(pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, dtype=object,
                                      engine="xlrd").values())
        if inicio.startswith((b"<", b"\xef\xbb\xbf<")) and b"<table" in conteudo[:200000].lower():
            # muitos sistemas exportam "xls" que na verdade é uma página HTML com uma tabela
            tabelas = pd.read_html(io.StringIO(_decodificar(conteudo)), header=None, flavor="lxml")
            grades = []
            for t in tabelas:
                if not isinstance(t.columns, pd.RangeIndex):         # cabeçalho em <th>: vira 1ª linha
                    cab = [" ".join(str(x) for x in c if not str(x).startswith("Unnamed")) if isinstance(c, tuple)
                           else c for c in t.columns]
                    t = pd.concat([pd.DataFrame([cab], columns=t.columns), t], ignore_index=True)
                t.columns = range(t.shape[1])
                grades.append(t.astype(object))
            return grades
    except ImportError as e:
        raise ArquivoIlegivel(f"O servidor não tem o leitor deste formato ({e}). Salve como .xlsx ou .csv.") from e
    except Exception as e:
        if nome.endswith((".xls", ".xlsx", ".xlsm")):
            raise ArquivoIlegivel("Não consegui abrir a planilha (arquivo corrompido ou protegido por senha?). "
                                  "Abra no Excel e use Salvar como → .xlsx.") from e
        raise ArquivoIlegivel(f"Não consegui ler o arquivo: {type(e).__name__}.") from e
    # texto: CSV / TXT (separador detectado)
    texto = _decodificar(conteudo)
    try:
        sep = csv.Sniffer().sniff(texto[:20000], delimiters=";,\t|").delimiter
    except csv.Error:
        sep = ";"
    try:
        return [pd.read_csv(io.StringIO(texto), sep=sep, header=None, dtype=object, keep_default_na=False,
                            engine="python", on_bad_lines="skip")]
    except Exception as e:
        raise ArquivoIlegivel("Não reconheci o formato. Envie .xlsx, .xls ou .csv.") from e


def ler_arquivo(conteudo: bytes, nome_arquivo: str, fonte: str | None = None) -> pd.DataFrame:
    """Lê CSV, Excel (.xlsx/.xls) ou "xls" em HTML; escolhe a aba e a linha de cabeçalho certas."""
    grades = _grades_do_arquivo(conteudo, nome_arquivo.lower())
    melhores = [_com_cabecalho(g, fonte) for g in grades]
    melhores = [m for m in melhores if not m[0].empty]
    if not melhores:
        raise ArquivoIlegivel("O arquivo está vazio ou não tem nenhuma tabela.")
    df, _ = max(melhores, key=lambda m: (m[1], len(m[0])))
    out = df.replace({"": None})
    out = out.where(out.notna(), None)
    out.attrs["linha_excel"] = True
    return out


def _mapear_por_palavras(normal: dict[str, str], escolhidas: dict, fonte: str) -> None:
    usadas = set(escolhidas.values())
    for campo, (tem, nao_tem) in PALAVRAS[fonte].items():
        if campo in escolhidas:
            continue
        for n, original in normal.items():
            if original in usadas:
                continue
            if any(k in n for k in tem) and not any(k in n for k in nao_tem):
                escolhidas[campo] = original
                usadas.add(original)
                break


def mapear_colunas(df: pd.DataFrame, fonte: str) -> tuple[pd.DataFrame, dict]:
    normal = {normalizar_cabecalho(c): c for c in df.columns}
    escolhidas: dict[str, str] = {}
    usadas: set[str] = set()
    for campo, sinonimos in COLUNAS[fonte].items():
        for s_ in sinonimos:
            if s_ in normal and normal[s_] not in usadas:
                escolhidas[campo] = normal[s_]
                usadas.add(normal[s_])
                break
    _mapear_por_palavras(normal, escolhidas, fonte)
    faltando = [DESCRICAO_CAMPO.get(c, c) for c in OBRIGATORIAS[fonte] if c not in escolhidas]
    if fonte == "AGENDAMENTOS" or "codigo" not in escolhidas:
        if not ({"nome", "cpf", "cns", "codigo"} & escolhidas.keys()):
            faltando.append("identificação do paciente (nome, CPF, CNS ou prontuário)")
    if faltando:
        raise ColunasAusentes("Não encontrei a coluna de: " + "; ".join(faltando)
                              + ". Colunas que encontrei no arquivo: " + ", ".join(f"“{c}”" for c in df.columns[:40])
                              + ". Renomeie o título da coluna na planilha (ex.: “Data”, “Unidade”, “Status”) e envie de novo.")
    saida = pd.DataFrame({campo: df[col] for campo, col in escolhidas.items()})
    # nº da linha como o usuário vê na planilha
    saida["_linha"] = ([int(i) + 1 for i in df.index] if df.attrs.get("linha_excel") else range(2, len(df) + 2))
    return saida, escolhidas


# ----------------------------------------------------------------------------
# Resolução de cadastros
# ----------------------------------------------------------------------------
def _tipo_ubs(nome_norm: str) -> str:
    if nome_norm.startswith("UBS"):
        return "UBS"
    if "ESPECIALIDADES" in nome_norm:
        return "CENTRO_ESPECIALIDADES"
    if nome_norm.startswith("CTA"):
        return "CTA"
    if nome_norm.startswith("ESPACO"):
        return "ESPACO"
    if "PRISIONAL" in nome_norm:
        return "PRISIONAL"
    return "OUTRO"


class ResolvedorUBS:
    PREFIXOS = ("UNIDADE BASICA DE SAUDE ", "UBS ", "US ")

    def __init__(self, conn: Connection, criar_novas: bool):
        self.conn, self.criar = conn, criar_novas
        self.mapa: dict[str, int] = {}
        for r in conn.execute(text("SELECT id, nome FROM ubs")):
            self._indexar(normalizar_texto(r.nome), r.id)
        for r in conn.execute(text("SELECT alias, ubs_id FROM ubs_alias")):
            self._indexar(r.alias, r.ubs_id)
        self.criadas: list[str] = []

    def _nucleo(self, n: str) -> str:
        for p in self.PREFIXOS:
            if n.startswith(p):
                return n[len(p):]
        return n

    def _indexar(self, n: str, i: int):
        self.mapa.setdefault(n, i)
        self.mapa.setdefault("#" + self._nucleo(n), i)

    def resolver(self, valor) -> int | None:
        n = normalizar_texto(valor)
        if not n or n in ("-", "SEM UNIDADE", "NAO INFORMADO"):
            return None
        if n in self.mapa:
            return self.mapa[n]
        if "#" + self._nucleo(n) in self.mapa:
            uid = self.mapa["#" + self._nucleo(n)]
            self.conn.execute(text("INSERT INTO ubs_alias (alias, ubs_id) VALUES (:a,:u) ON CONFLICT DO NOTHING"),
                              {"a": n, "u": uid})
            self.mapa[n] = uid
            return uid
        if not self.criar:
            return None
        nome = " ".join(str(valor).split()).upper()
        uid = self.conn.execute(text("""INSERT INTO ubs (nome, tipo) VALUES (:n, :t)
                                        ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id"""),
                                {"n": nome, "t": _tipo_ubs(n)}).scalar()
        self._indexar(n, uid)
        self.criadas.append(nome)
        return uid


def _catalogo_exames(conn: Connection) -> dict[str, int]:
    """Sigla → id (sem espaços), aceitando também alias e o nome completo do exame."""
    mapa = {}
    for r in conn.execute(text("SELECT id, sigla, nome FROM exames")):
        mapa.setdefault(normalizar_texto(r.sigla).replace(" ", ""), r.id)
    for r in conn.execute(text("SELECT alias, exame_id FROM exame_alias")):
        mapa.setdefault(normalizar_texto(r.alias).replace(" ", ""), r.exame_id)
    for r in conn.execute(text("SELECT id, nome FROM exames")):
        mapa.setdefault(normalizar_texto(r.nome).replace(" ", ""), r.id)
    return mapa


def _cadastrar_exame(conn: Connection, sigla_bruta, nome_bruto) -> int:
    """Exame que ainda não está no catálogo: cadastra (setor 'Não classificado') em vez de perder a linha."""
    sigla = " ".join(str(sigla_bruta).split()).upper()[:40]
    nome = " ".join(str(nome_bruto or sigla_bruta).split())[:200]
    return conn.execute(text("""INSERT INTO exames (sigla, nome) VALUES (:s, :n)
                                ON CONFLICT (sigla) DO UPDATE SET sigla = EXCLUDED.sigla RETURNING id"""),
                        {"s": sigla, "n": nome}).scalar()


def _pseudo(row, rel: Relatorio, segredo: str, estrategia) -> str | None:
    nasc = parse_data(row.get("nascimento")) if "nascimento" in row else None
    ident, _ = chave_paciente(cpf=row.get("cpf"), nome=row.get("nome"), nascimento=nasc,
                              cns=row.get("cns"), codigo=row.get("codigo"), estrategia=estrategia)
    if not ident:
        rel.registrar(row["_linha"], "paciente", row.get("nome") or row.get("codigo"),
                      "Paciente sem identificação suficiente (CPF, nome+nascimento, CNS ou código)")
        return None
    return pseudo_id(ident, segredo)


def _decimal(valor) -> Decimal | None:
    s = str(valor or "").replace("R$", "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _datahora(valor) -> datetime | None:
    if valor is None or str(valor).strip() == "" or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (datetime, pd.Timestamp)):
        return pd.Timestamp(valor).to_pydatetime()
    s = str(valor).strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    d = parse_data(s)
    return datetime.combine(d, datetime.min.time()) if d else None


def _hora_da_data(valor):
    """Coluna 'Data/Hora' sem coluna de horário separada: aproveita a hora."""
    if isinstance(valor, (datetime, pd.Timestamp)):
        t = pd.Timestamp(valor).time()
        return t.replace(second=0, microsecond=0) if (t.hour or t.minute) else None
    m = re.search(r"\b(\d{1,2}:\d{2})", str(valor or ""))
    return parse_hora(m.group(1)) if m else None


def _resumir_ubs(rel: Relatorio, desconhecidas: dict, efeito: str) -> None:
    """Uma linha por UBS não reconhecida, com quantas linhas foram afetadas (o detalhe fica na lista)."""
    for nome, n in sorted(desconhecidas.items(), key=lambda x: -x[1])[:30]:
        if normalizar_texto(nome) in ("", "SEM UNIDADE", "NAO INFORMADO") or nome in ("-", "(vazia)"):
            rel.registrar(None, "ubs", nome, f"Unidade vazia na planilha (“{nome}”): {n} linhas {efeito}. "
                                             "Só dá para incluir corrigindo a unidade na planilha de origem", "AVISO")
        else:
            rel.registrar(None, "ubs", nome, f"UBS não reconhecida: {n} linhas {efeito}. Reimporte marcando "
                                             "“Cadastrar automaticamente UBS” ou cadastre um alias em Configurações",
                          "AVISO")


# ----------------------------------------------------------------------------
# Identificação do paciente: usa a mesma chave do outro lado (agenda × Worklab)
# ----------------------------------------------------------------------------
ROTULO_CHAVE = {"cpf": "CPF", "nome_nascimento": "nome + data de nascimento", "cns": "CNS", "codigo": "código/prontuário"}


def _chave_linha(row: dict, tipo: str) -> str | None:
    nasc = parse_data(row.get("nascimento")) if row.get("nascimento") is not None else None
    ident, _ = chave_paciente(cpf=row.get("cpf"), nome=row.get("nome"), nascimento=nasc,
                              cns=row.get("cns"), codigo=row.get("codigo"), estrategia=(tipo,))
    return ident


def escolher_estrategia(conn: Connection, tabela: pd.DataFrame, fonte: str, segredo: str,
                        padrao: tuple, rel: Relatorio) -> tuple:
    """Escolhe a identificação do paciente que mais coincide com o outro lado no mesmo período.

    Ex.: a agenda tem CPF e nome+nascimento, o Worklab só nome+nascimento → os dois usam nome+nascimento,
    e o cruzamento "compareceu × foi atendido" funciona. Se o outro lado ainda não foi importado,
    prefere nome+nascimento (o dado mais comum nos dois sistemas).
    """
    cols = set(tabela.columns)
    disponiveis = [t for t in padrao if (t == "nome_nascimento" and {"nome", "nascimento"} <= cols)
                   or (t != "nome_nascimento" and t in cols)]
    if not disponiveis:
        return padrao
    amostra = tabela.drop_duplicates([c for c in ("cpf", "nome", "nascimento", "cns", "codigo") if c in cols])
    amostra = amostra.head(4000).to_dict("records")
    preferida = ("nome_nascimento",) if "nome_nascimento" in disponiveis else ()
    ordem_sem_outro = preferida + tuple(t for t in padrao if t not in preferida)

    datas = [d for d in (parse_data(v) for v in tabela["data"].head(20000)) if d]
    outra = "atendimentos" if fonte == "AGENDAMENTOS" else "agendamentos"
    existentes: set[str] = set()
    if datas:
        existentes = {r[0] for r in conn.execute(text(f"""
            SELECT DISTINCT paciente_pseudo FROM {outra}
            WHERE data BETWEEN :i AND :f AND paciente_pseudo IS NOT NULL"""),
            {"i": min(datas) - timedelta(days=10), "f": max(datas) + timedelta(days=10)})}
    nome_outra = "atendimentos do Worklab" if outra == "atendimentos" else "agendamentos"
    if not existentes:
        escolhida = ordem_sem_outro[0] if ordem_sem_outro[0] in disponiveis else disponiveis[0]
        rel.registrar(None, "paciente", ROTULO_CHAVE[escolhida],
                      f"Identificação do paciente: {ROTULO_CHAVE[escolhida]} (ainda não há {nome_outra} deste "
                      "período para comparar)", "AVISO", info=True)
        return (escolhida,) + tuple(t for t in ordem_sem_outro if t != escolhida)

    melhor, melhor_pct = None, -1.0
    for t in disponiveis:
        chaves = {k for k in (_chave_linha(r, t) for r in amostra) if k}
        if not chaves:
            continue
        pct = 100.0 * len({pseudo_id(k, segredo) for k in chaves} & existentes) / len(chaves)
        if pct > melhor_pct:
            melhor, melhor_pct = t, pct
    if melhor is None:
        return padrao
    if melhor_pct < 20 and len(amostra) >= 30:      # arquivo pequeno: porcentagem não diz nada
        rel.registrar(None, "paciente", ROTULO_CHAVE[melhor],
                      f"Só {melhor_pct:.0f}% dos pacientes deste arquivo aparecem nos {nome_outra} do mesmo período: "
                      "as duas planilhas podem identificar o paciente de jeitos diferentes (ex.: uma só com CPF, a "
                      "outra só com nome). O cruzamento agendamento × atendimento ficará incompleto.", "AVISO")
    else:
        rel.registrar(None, "paciente", ROTULO_CHAVE[melhor],
                      f"Identificação do paciente: {ROTULO_CHAVE[melhor]} — {melhor_pct:.0f}% dos pacientes deste "
                      f"arquivo também estão nos {nome_outra} do período", "AVISO", info=True)
    return (melhor,) + tuple(t for t in ordem_sem_outro if t != melhor)


# ----------------------------------------------------------------------------
# Tratamento por fonte
# ----------------------------------------------------------------------------
def tratar_agendamentos(df, rel, conn, segredo, criar_ubs, estrategia, hoje: date) -> pd.DataFrame:
    ubs = ResolvedorUBS(conn, criar_ubs)
    if "observacoes" in df.columns:
        preenchidas = int((df["observacoes"].astype(str).str.strip() != "").sum())
        if preenchidas:
            rel.registrar(None, "observacoes", f"{preenchidas} linhas",
                          "Coluna de observações descartada: texto livre pode conter dados de saúde/identificação",
                          "AVISO")
    limpos, desconhecidas, canceladas = [], {}, 0
    for row in df.to_dict("records"):
        ln = row["_linha"]
        d = parse_data(row.get("data"))
        if d is None:
            rel.registrar(ln, "data", row.get("data"), "Data inválida ou vazia"); continue
        if d.year < 2015 or d > hoje + timedelta(days=365):
            rel.registrar(ln, "data", d, "Data fora do intervalo plausível"); continue
        ubs_id = ubs.resolver(row.get("ubs"))
        if ubs_id is None:
            nome_ubs = " ".join(str(row.get("ubs") or "").split()) or "(vazia)"
            desconhecidas[nome_ubs] = desconhecidas.get(nome_ubs, 0) + 1
            rel.registrar(ln, "ubs", row.get("ubs"),
                          "UBS não cadastrada ou vazia — cadastre a UBS ou um alias em Configurações"); continue
        status_txt = row.get("status")
        status = mapear_valor(status_txt, STATUS_AGEND)
        if status == "IGNORAR":
            canceladas += 1
            continue
        if status is None:
            if normalizar_texto(status_txt):
                rel.registrar(ln, "status", status_txt, "Status desconhecido"); continue
            status = "AGENDADO" if d > hoje else "PENDENTE"
            if status == "PENDENTE":
                rel.registrar(ln, "status", "", "Status vazio em data passada — registrado como PENDENTE", "AVISO")
        if status in ("COMPARECEU", "FALTOU") and d > hoje:
            rel.registrar(ln, "status", status, "Comparecimento/falta registrado em data futura"); continue
        tipo = mapear_valor(row.get("tipo"), TIPO_AGEND) or "ROTINA"
        if row.get("tipo") and not mapear_valor(row.get("tipo"), TIPO_AGEND):
            rel.registrar(ln, "tipo", row.get("tipo"), "Tipo desconhecido — assumido ROTINA", "AVISO")
        hora = parse_hora(row.get("horario")) if "horario" in row else _hora_da_data(row.get("data"))
        if "horario" in row and str(row.get("horario") or "").strip() and hora is None:
            rel.registrar(ln, "horario", row.get("horario"), "Horário inválido — ignorado", "AVISO")
        pac = _pseudo(row, rel, segredo, estrategia)
        if pac is None:
            continue
        limpos.append({"_linha": ln, "data": d, "horario": hora, "ubs_id": ubs_id,
                       "paciente_pseudo": pac, "tipo": tipo, "status": status})
    out = pd.DataFrame(limpos, columns=["_linha", "data", "horario", "ubs_id", "paciente_pseudo", "tipo", "status"])
    dup = out.duplicated(["data", "ubs_id", "paciente_pseudo"], keep="first")
    for ln in out.loc[dup, "_linha"]:
        rel.registrar(ln, "linha", None, "Agendamento duplicado no próprio arquivo (mesmo paciente, UBS e data)", "AVISO")
    rel.duplicadas += int(dup.sum())
    for nome in ubs.criadas:
        rel.registrar(None, "ubs", nome, "Nova UBS cadastrada automaticamente — revise o nome", "AVISO")
    _resumir_ubs(rel, desconhecidas, "rejeitadas")
    horas = out.loc[~dup, "horario"].dropna()
    if len(horas) >= 30:
        cont = horas.value_counts()
        padrao, n_padrao = cont.index[0], int(cont.iloc[0])
        if n_padrao / len(horas) >= 0.9 and len(cont) > 1:
            outros = ", ".join(f"{h:%H:%M} ({n})" for h, n in cont.iloc[1:6].items())
            rel.registrar(None, "horario", f"{len(horas) - n_padrao} linhas",
                          f"Horário diferente do padrão {padrao:%H:%M}: {outros}{'…' if len(cont) > 6 else ''} — "
                          "provável erro de digitação; mantido como veio na planilha", "AVISO")
    if canceladas:
        rel.registrar(None, "status", f"{canceladas} linhas",
                      "Agendamentos cancelados/desmarcados/remarcados — ficam fora dos indicadores", "AVISO", info=True)
    return out[~dup]


def tratar_worklab(df, rel, conn, segredo, criar_ubs, estrategia, hoje: date) -> pd.DataFrame:
    ubs = ResolvedorUBS(conn, criar_ubs)
    exames = _catalogo_exames(conn)
    limpos, desconhecidas, novos_exames = [], {}, {}
    freq_sigla = df["exame"].map(lambda v: normalizar_texto(v).replace(" ", "")).value_counts().to_dict()
    for row in df.to_dict("records"):
        ln = row["_linha"]
        cod = normalizar_texto(row.get("atendimento")).replace(" ", "")
        if not cod:
            rel.registrar(ln, "atendimento", row.get("atendimento"), "Código de atendimento vazio"); continue
        d = parse_data(row.get("data"))
        if d is None or d.year < 2015 or d > hoje:
            rel.registrar(ln, "data", row.get("data"), "Data de atendimento inválida, vazia ou futura"); continue
        sig = normalizar_texto(row.get("exame")).replace(" ", "")
        if not sig:
            rel.registrar(ln, "exame", row.get("exame"), "Exame vazio"); continue
        exame_id = exames.get(sig)
        if exame_id is None and row.get("nome_exame"):
            exame_id = exames.get(normalizar_texto(row.get("nome_exame")).replace(" ", ""))
        if exame_id is None and freq_sigla.get(sig, 0) < getattr(rel, "min_freq_exame_novo", 3):
            # sigla rara: pode ser lixo do relatório ("TOTAL", "CONFERIDO"…) — não vira exame
            rel.registrar(ln, "exame", row.get("exame"), "Exame não catalogado (sigla desconhecida)"); continue
        if exame_id is None:
            exame_id = _cadastrar_exame(conn, row.get("exame"), row.get("nome_exame"))
            exames[sig] = exame_id
            novos_exames[str(row.get("exame")).strip()] = freq_sigla.get(sig, 0)
        ubs_txt = row.get("ubs")
        ubs_id = ubs.resolver(ubs_txt) if "ubs" in row else None
        if ubs_id is None and normalizar_texto(ubs_txt) not in ("", "-"):
            nome_ubs = " ".join(str(ubs_txt).split())
            desconhecidas[nome_ubs] = desconhecidas.get(nome_ubs, 0) + 1
        qtd = 1
        if str(row.get("quantidade") or "").strip():
            try:
                qtd = int(float(str(row["quantidade"]).replace(",", ".")))
            except ValueError:
                qtd = 0
            if qtd <= 0:
                rel.registrar(ln, "quantidade", row.get("quantidade"), "Quantidade inválida"); continue
        valor = _decimal(row.get("valor")) if "valor" in row else None
        if valor is not None and valor < 0:
            rel.registrar(ln, "valor", row.get("valor"), "Valor negativo — ignorado", "AVISO"); valor = None
        situacao = mapear_valor(row.get("situacao"), SITUACAO_EXAME) or "LIBERADO"
        lib = _datahora(row.get("liberado_em")) if "liberado_em" in row else None
        if lib is not None and lib.date() < d:
            rel.registrar(ln, "liberado_em", lib, "Liberação anterior ao atendimento — ignorada", "AVISO"); lib = None
        pac = _pseudo(row, rel, segredo, estrategia)
        if pac is None:
            continue
        limpos.append({"_linha": ln, "codigo_worklab": cod, "data": d, "paciente_pseudo": pac, "ubs_id": ubs_id,
                       "convenio": (normalizar_texto(row.get("convenio")) or "SUS")[:40],
                       "exame_id": exame_id, "quantidade": qtd, "valor": valor,
                       "situacao": situacao, "liberado_em": lib})
    for sig, n in sorted(novos_exames.items(), key=lambda x: -x[1]):
        rel.registrar(None, "exame", sig, f"Exame novo cadastrado automaticamente no catálogo ({n} linhas) — "
                                          "defina o setor em Configurações se quiser", "AVISO")
    _resumir_ubs(rel, desconhecidas, "importadas sem UBS")
    cols = ["_linha", "codigo_worklab", "data", "paciente_pseudo", "ubs_id", "convenio", "exame_id",
            "quantidade", "valor", "situacao", "liberado_em"]
    out = pd.DataFrame(limpos, columns=cols)
    if out.empty:
        return out
    # Um atendimento não pode ter dois pacientes ou duas datas
    cab = out.groupby("codigo_worklab").agg(n_pac=("paciente_pseudo", "nunique"), n_data=("data", "nunique"))
    conflito = cab[(cab.n_pac > 1) | (cab.n_data > 1)].index
    for ln in out.loc[out.codigo_worklab.isin(conflito), "_linha"]:
        rel.registrar(ln, "atendimento", None, "Mesmo atendimento com paciente ou data diferentes")
    out = out[~out.codigo_worklab.isin(conflito)]
    dup = out.duplicated(["codigo_worklab", "exame_id"], keep="first")
    for ln in out.loc[dup, "_linha"]:
        rel.registrar(ln, "linha", None, "Exame repetido no mesmo atendimento", "AVISO")
    rel.duplicadas += int(dup.sum())
    for nome in ubs.criadas:
        rel.registrar(None, "ubs", nome, "Nova UBS cadastrada automaticamente — revise o nome", "AVISO")
    return out[~dup]


# ----------------------------------------------------------------------------
# Gravação (em tabela temporária + INSERT … ON CONFLICT para deduplicar contra o banco)
# ----------------------------------------------------------------------------
def _carregar_temp(conn: Connection, nome: str, df: pd.DataFrame, colunas: dict[str, str]):
    conn.execute(text(f"CREATE TEMP TABLE {nome} ({', '.join(f'{c} {t}' for c, t in colunas.items())}) ON COMMIT DROP"))
    if df.empty:
        return
    registros = df[list(colunas)].astype(object).where(df[list(colunas)].notna(), None).to_dict("records")
    conn.execute(text(f"INSERT INTO {nome} ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)})"),
                 registros)


def gravar_agendamentos(conn, df, imp_id) -> int:
    _carregar_temp(conn, "tmp_agend", df, {"data": "date", "horario": "time", "ubs_id": "int",
                                           "paciente_pseudo": "text", "tipo": "text", "status": "text"})
    return conn.execute(text("""
        INSERT INTO agendamentos (data, horario, ubs_id, paciente_pseudo, tipo, status, importacao_id)
        SELECT data, horario, ubs_id, paciente_pseudo, tipo, status, :imp FROM tmp_agend
        ON CONFLICT (data, ubs_id, paciente_pseudo) DO NOTHING"""), {"imp": imp_id}).rowcount


def gravar_worklab(conn, df, imp_id) -> int:
    _carregar_temp(conn, "tmp_wl", df, {"codigo_worklab": "text", "data": "date", "paciente_pseudo": "text",
                                        "ubs_id": "int", "convenio": "text", "exame_id": "int",
                                        "quantidade": "int", "valor": "numeric", "situacao": "text",
                                        "liberado_em": "timestamptz"})
    conn.execute(text("""
        INSERT INTO atendimentos (codigo_worklab, data, paciente_pseudo, ubs_id, convenio, importacao_id)
        SELECT DISTINCT ON (codigo_worklab) codigo_worklab, data, paciente_pseudo, ubs_id, convenio, :imp
        FROM tmp_wl ORDER BY codigo_worklab
        ON CONFLICT (codigo_worklab) DO NOTHING"""), {"imp": imp_id})
    return conn.execute(text("""
        INSERT INTO atendimento_exames (atendimento_id, exame_id, quantidade, valor, situacao, liberado_em)
        SELECT a.id, w.exame_id, w.quantidade, w.valor, w.situacao, w.liberado_em
        FROM tmp_wl w JOIN atendimentos a ON a.codigo_worklab = w.codigo_worklab
        ON CONFLICT (atendimento_id, exame_id) DO NOTHING""")).rowcount


TRATADORES = {"AGENDAMENTOS": (tratar_agendamentos, gravar_agendamentos),
              "WORKLAB": (tratar_worklab, gravar_worklab)}


# ----------------------------------------------------------------------------
# Ponto de entrada
# ----------------------------------------------------------------------------
def _hash_disponivel(engine: Engine, h: str) -> str:
    """O mesmo arquivo só entra de novo se a importação anterior teve linhas rejeitadas
    (ex.: UBS que faltava cadastrar). As linhas que já entraram não duplicam (ON CONFLICT)."""
    with engine.connect() as c:
        for k in range(50):
            hk = h if k == 0 else hashlib.sha256(f"{h}#{k}".encode()).hexdigest()
            r = c.execute(text("SELECT id, status, rejeitadas FROM importacoes WHERE hash_arquivo = :h"),
                          {"h": hk}).first()
            if r is None or r.status == "FALHOU":
                return hk
            if not r.rejeitadas:
                raise ArquivoJaImportado(r.id)
    raise ArquivoJaImportado(r.id)


def importar(engine: Engine, fonte: str, conteudo: bytes, nome_arquivo: str, *, segredo: str,
             usuario_id: int | None = None, criar_ubs: bool = False,
             estrategia=ESTRATEGIA_PADRAO, hoje: date | None = None, df: pd.DataFrame | None = None,
             notas: list[str] | None = None, min_freq_exame_novo: int = 3) -> Relatorio:
    fonte = fonte.upper()
    if fonte not in TRATADORES:
        raise ValueError(f"Fonte desconhecida: {fonte}")
    hoje = hoje or date.today()
    h = _hash_disponivel(engine, hashlib.sha256(conteudo).hexdigest())
    rel = Relatorio(fonte=fonte, arquivo=nome_arquivo, hash_arquivo=h)
    rel.min_freq_exame_novo = min_freq_exame_novo

    bruto = df if df is not None else ler_arquivo(conteudo, nome_arquivo, fonte)
    bruto = bruto.dropna(how="all")
    tabela, rel.colunas_reconhecidas = mapear_colunas(bruto, fonte)
    rel.linhas_lidas = len(tabela)
    rel.registrar(None, "colunas", None, "Colunas usadas: " + "; ".join(
        f"{ROTULO_CAMPO.get(campo, campo)} ← “{col}”" for campo, col in rel.colunas_reconhecidas.items()
        if campo not in CAMPOS_PESSOAIS)
        + (" · identificação: " + ", ".join(c for c in ("nome", "cpf", "cns", "nascimento", "codigo")
                                             if c in rel.colunas_reconhecidas) if CAMPOS_PESSOAIS & rel.colunas_reconhecidas.keys() else ""),
        "AVISO", info=True)
    for n in notas or []:
        rel.registrar(None, "lote", None, n, "AVISO", info=True)
    tratar, gravar = TRATADORES[fonte]

    try:
        with engine.begin() as conn:
            if usuario_id:
                conn.execute(text("SELECT set_config('app.usuario_id', :u, true)"), {"u": str(usuario_id)})
            conn.execute(text("DELETE FROM importacoes WHERE hash_arquivo = :h AND status = 'FALHOU'"), {"h": h})
            rel.importacao_id = conn.execute(text("""
                INSERT INTO importacoes (fonte, arquivo, hash_arquivo, usuario_id, linhas_lidas)
                VALUES (:f, :a, :h, :u, :n) RETURNING id"""),
                {"f": fonte, "a": nome_arquivo[:200], "h": h, "u": usuario_id, "n": rel.linhas_lidas}).scalar()

            if estrategia == ESTRATEGIA_PADRAO:   # automático: a mesma chave do outro lado do cruzamento
                estrategia = escolher_estrategia(conn, tabela, fonte, segredo, ESTRATEGIA_PADRAO, rel)
            limpo = tratar(tabela, rel, conn, segredo, criar_ubs, estrategia, hoje)
            rel.rejeitadas = len({e["linha"] for e in rel.erros if e["severidade"] == "REJEITADA"})
            inseridas = gravar(conn, limpo, rel.importacao_id)
            rel.inseridas = inseridas
            # o que passou na validação mas já existia no banco também é duplicidade
            rel.duplicadas += max(len(limpo) - inseridas, 0)
            if not limpo.empty:
                rel.periodo_inicio, rel.periodo_fim = min(limpo["data"]), max(limpo["data"])
            rel.status = "CONCLUIDA_COM_ALERTAS" if rel.tem_alertas else "CONCLUIDA"

            if rel.erros:
                conn.execute(text("""INSERT INTO importacao_erros (importacao_id, linha, campo, valor, problema, severidade)
                                     VALUES (:i, :linha, :campo, :valor, :problema, :severidade)"""),
                             [{"i": rel.importacao_id, **{k: v for k, v in e.items() if k != "info"}}
                              for e in rel.erros[:5000]])
            conn.execute(text("""UPDATE importacoes SET inseridas=:ins, duplicadas=:dup, rejeitadas=:rej,
                                 periodo_inicio=:pi, periodo_fim=:pf, status=:st, concluido_em=now() WHERE id=:i"""),
                         {"ins": rel.inseridas, "dup": rel.duplicadas, "rej": rel.rejeitadas,
                          "pi": rel.periodo_inicio, "pf": rel.periodo_fim, "st": rel.status, "i": rel.importacao_id})
    except Exception:
        rel.status = "FALHOU"
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO importacoes (fonte, arquivo, hash_arquivo, usuario_id, linhas_lidas, status, concluido_em)
                                 VALUES (:f, :a, :h, :u, :n, 'FALHOU', now()) ON CONFLICT (hash_arquivo) DO NOTHING"""),
                         {"f": fonte, "a": nome_arquivo[:200], "h": h, "u": usuario_id, "n": rel.linhas_lidas})
        raise
    atualizar_camada_analitica(engine)
    return rel


def atualizar_camada_analitica(engine: Engine) -> None:
    """Recalcula as views materializadas (004_desempenho.sql) depois de mudar agendamentos/atendimentos."""
    with engine.begin() as conn:
        if conn.execute(text("SELECT to_regproc('analitico.atualizar')")).scalar():
            conn.execute(text("SELECT analitico.atualizar()"))
