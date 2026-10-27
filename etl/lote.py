"""Importação em lote: vários arquivos de uma vez (ex.: a pasta saida_bi do mês).

Reconhece cada arquivo pelo conteúdo das colunas:
  AGENDAMENTOS  tem status (compareceu/faltou)              ex.: fAgendamentos_Setembro.csv
  ATENDIMENTOS  tem nº do atendimento + paciente, sem exame ex.: fAtendimentos_Setembro.csv
  EXAMES        tem nº do atendimento + exame/sigla         ex.: fExames_Setembro.csv
  UNIDADES      tem ID_Unidade + nome da unidade            ex.: dUnidades.csv

Depois:
  • junta EXAMES + ATENDIMENTOS pelo nº do atendimento (vira um arquivo Worklab completo);
  • troca ID_Unidade pelo nome da UBS usando UNIDADES;
  • escolhe a identificação do paciente que mais coincide entre agenda e Worklab — dando preferência
    ao ID do paciente, que é como os meses anteriores foram importados (o mesmo paciente recebe o
    mesmo código em todos os meses);
  • importa agenda e Worklab pelo pipeline normal (validação, anonimização, deduplicação).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from .anonimizacao import cpf_valido
from .importador import (COLUNAS, STATUS_AGEND, ArquivoIlegivel, ArquivoJaImportado, ColunasAusentes, importar,
                         ler_arquivo)
from .normalizacao import mapear_valor, normalizar_cabecalho, normalizar_texto, parse_data, so_digitos

NOMES_PAPEL = {"AGENDAMENTOS": "agendamentos", "ATENDIMENTOS": "atendimentos do Worklab",
               "EXAMES": "exames do Worklab", "UNIDADES": "cadastro de unidades", "DESCONHECIDO": "não reconhecido"}
# nomes canônicos que o importador reconhece de volta
CANONICO = {"data": "Data", "horario": "Horario", "ubs": "Unidade", "tipo": "Tipo", "status": "Status",
            "atendimento": "Atendimento", "exame": "Sigla", "nome_exame": "Nome do exame", "quantidade": "Quantidade",
            "valor": "Valor", "situacao": "Situacao", "liberado_em": "Liberado em", "convenio": "Convenio"}
ID_PAC = ("id_paciente", "codigo_paciente", "cod_paciente", "prontuario", "paciente", "cod_pac", "idpaciente")


@dataclass
class Arquivo:
    nome: str
    conteudo: bytes
    df: pd.DataFrame | None = None
    papel: str = "DESCONHECIDO"
    erro: str | None = None


@dataclass
class Lote:
    arquivos: list[Arquivo]
    agenda: pd.DataFrame | None = None
    worklab: pd.DataFrame | None = None
    notas: list[str] = field(default_factory=list)       # vão para o relatório da importação (Info)
    problemas: list[str] = field(default_factory=list)   # impedem a importação de uma das partes
    identificacao: str | None = None
    cruzamento_pct: float | None = None
    opcoes: list = field(default_factory=list)            # pares de colunas avaliados (para o verificador)


def _n(c) -> str:
    return normalizar_cabecalho(c)


def _achar(df: pd.DataFrame, nomes, contem=(), nao_contem=()) -> str | None:
    norm = {_n(c): c for c in df.columns}
    for x in nomes:
        if x in norm:
            return norm[x]
    for n, c in norm.items():
        if contem and any(k in n for k in contem) and not any(k in n for k in nao_contem):
            return c
    return None


def _col_atendimento(df):
    return _achar(df, COLUNAS["WORKLAB"]["atendimento"], ("atendimento", "requisicao"), ("data", "hora", "tipo", "local"))


def _col_exame(df):
    return _achar(df, ["sigla", "sigla_exame", "sigla_do_exame", "mnemonico", "cod_exame", "codigo_exame"],
                  ("sigla", "mnemonico"))


def _col_status(df):
    return _achar(df, COLUNAS["AGENDAMENTOS"]["status"])


def _col_id_unidade(df):
    return _achar(df, ["id_unidade", "cod_unidade", "codigo_unidade", "id_ubs", "cod_ubs"])


def _col_nome_unidade(df):
    return _achar(df, ["unidade", "ubs", "nome_unidade", "nome_da_unidade", "unidade_de_saude", "estabelecimento",
                       "posto", "nome"])


def classificar(df: pd.DataFrame) -> str:
    atend, exame, status = _col_atendimento(df), _col_exame(df), _col_status(df)
    if exame is None:
        exame = _achar(df, ["exame"])
    if atend and exame:
        return "EXAMES"
    if status and not atend:
        return "AGENDAMENTOS"
    if atend:
        return "ATENDIMENTOS"
    if _col_id_unidade(df) and _col_nome_unidade(df) and len(df) < 5000:
        return "UNIDADES"
    return "DESCONHECIDO"


def _chave_txt(v) -> str:
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _trocar_id_unidade(df: pd.DataFrame, unidades: pd.DataFrame | None, lote: Lote, rotulo: str) -> pd.DataFrame:
    """Se o arquivo só tem ID_Unidade, troca pelo nome usando o cadastro de unidades."""
    if _achar(df, ["unidade", "ubs", "nome_unidade", "unidade_de_saude", "estabelecimento", "posto", "origem"]):
        return df
    idc = _col_id_unidade(df)
    if not idc:
        return df
    if unidades is None:
        lote.problemas.append(f"O arquivo de {rotulo} tem só o código da unidade ({idc}). "
                              "Envie junto o arquivo de unidades (ex.: dUnidades.csv).")
        return df
    mapa = {_chave_txt(i): n for i, n in zip(unidades[_col_id_unidade(unidades)], unidades[_col_nome_unidade(unidades)])}
    df = df.copy()
    df["Unidade"] = df[idc].map(lambda v: mapa.get(_chave_txt(v)))
    sem = int(df["Unidade"].isna().sum())
    lote.notas.append(f"{rotulo}: nome da UBS obtido do cadastro de unidades pelo código “{idc}”"
                      + (f" ({sem} linhas com código sem cadastro)" if sem else ""))
    return df


def _juntar_worklab(exames: pd.DataFrame, atend: pd.DataFrame | None, lote: Lote) -> pd.DataFrame:
    if atend is None:
        return exames
    ke, ka = _col_atendimento(exames), _col_atendimento(atend)
    extra = [c for c in atend.columns if c != ka and _n(c) not in {_n(x) for x in exames.columns}]
    a = atend.drop_duplicates(ka)[[ka] + extra].copy()
    a["__k"] = a[ka].map(_chave_txt)
    e = exames.copy()
    e["__k"] = e[ke].map(_chave_txt)
    out = e.merge(a.drop(columns=[ka]), on="__k", how="left").drop(columns="__k")
    sem = int((~e["__k"].isin(set(a["__k"]))).sum()) if len(e) else 0
    lote.notas.append(f"Exames ligados aos atendimentos pelo nº do atendimento (“{ke}” × “{ka}”): "
                      f"{len(e) - sem} de {len(e)} exames encontraram o atendimento")
    return out


def _candidatas(df: pd.DataFrame, so_compareceu: bool) -> dict[str, tuple[str, set, object]]:
    """Possíveis identificações do paciente → (tipo, conjunto de chaves, como montar a coluna)."""
    base = df
    if so_compareceu:
        st = _col_status(df)
        if st:
            base = df[df[st].map(lambda v: mapear_valor(v, STATUS_AGEND) == "COMPARECEU")]
    out = {}
    for c in df.columns:
        n = _n(c)
        if n in ID_PAC or (("paciente" in n or "prontuario" in n) and not any(k in n for k in ("nasc", "sexo", "idade", "mae"))):
            out[f"ID:{c}"] = ("codigo", {normalizar_texto(v) for v in base[c] if normalizar_texto(v)}, c)
    cpf = _achar(df, COLUNAS["AGENDAMENTOS"]["cpf"], ("cpf",))
    if cpf:
        out["CPF"] = ("cpf", {so_digitos(v) for v in base[cpf] if cpf_valido(so_digitos(v))}, cpf)
    cns = _achar(df, COLUNAS["AGENDAMENTOS"]["cns"], ("cns",))
    if cns:
        out["CNS"] = ("cns", {so_digitos(v) for v in base[cns] if len(so_digitos(v)) == 15}, cns)
    nome = _achar(df, ["nome", "nome_paciente", "nome_do_paciente", "paciente"], ("nome",), ("mae", "pai", "exame", "unidade"))
    nasc = _achar(df, COLUNAS["AGENDAMENTOS"]["nascimento"], ("nasc",))
    if nome and nasc:
        chaves = set()
        for a, b in zip(base[nome], base[nasc]):
            d = parse_data(b)
            if normalizar_texto(a) and d and len(normalizar_texto(a).split()) >= 2:
                chaves.add(f"{normalizar_texto(a)}|{d}")
        out["NN"] = ("nome_nascimento", chaves, (nome, nasc))
    return {k: v for k, v in out.items() if v[1]}


ROTULO_TIPO = {"codigo": "ID do paciente", "cpf": "CPF", "cns": "CNS", "nome_nascimento": "nome + data de nascimento"}


PREFIXO = {"codigo": "COD:", "cpf": "CPF:", "cns": "CNS:", "nome_nascimento": "NN:"}


def historico_do_banco(engine, segredo: str):
    """Função que diz quanto % das chaves de um arquivo já existe nos meses anteriores (mesma pseudonimização).

    Serve para setembro usar a MESMA identificação de junho–agosto: assim o paciente que volta em outro mês
    é reconhecido como a mesma pessoa.
    """
    from sqlalchemy import text
    from .anonimizacao import pseudo_id
    with engine.connect() as c:
        bases = {"AGENDAMENTOS": {r[0] for r in c.execute(text("SELECT DISTINCT paciente_pseudo FROM agendamentos"))},
                 "WORKLAB": {r[0] for r in c.execute(text("SELECT DISTINCT paciente_pseudo FROM atendimentos"))}}

    def pct(lado: str, tipo: str, chaves: set) -> float | None:
        base = bases.get(lado)
        if not base or not chaves:
            return None
        try:
            achou = sum(1 for k in chaves if pseudo_id(PREFIXO[tipo] + k, segredo) in base)
        except Exception:
            return None
        return 100.0 * achou / len(chaves)
    return pct


def _escolher_identificacao(ag: pd.DataFrame | None, wl: pd.DataFrame | None, lote: Lote, historico=None):
    """Devolve (escolha_agenda, escolha_worklab) — cada uma (tipo, coluna/colunas).

    Critério: cruzamento agenda × Worklab no mês + continuidade com os meses já importados
    (pontuação = % de cruzamento + média do % de pacientes reconhecidos no histórico).
    """
    ca = _candidatas(ag, True) if ag is not None else {}
    cw = _candidatas(wl, False) if wl is not None else {}
    ca_todos = _candidatas(ag, False) if ag is not None else {}

    def hist(lado, cand, k):
        if not historico:
            return None
        chaves = (ca_todos.get(k) or cand[k])[1] if lado == "AGENDAMENTOS" else cand[k][1]
        return historico(lado, cand[k][0], chaves)

    def col(c):
        return c if isinstance(c, str) else " + ".join(c)

    if not ca or not cw:
        um, lado = (ca, "AGENDAMENTOS") if ca else (cw, "WORKLAB")
        if not um:
            return None, None
        # Só um lado veio (ex.: exames de maio, com a agenda de maio já no banco): compara com os DOIS lados
        # do banco — o que importa é a chave bater com a agenda já importada, não só com o Worklab antigo.
        outro = "WORKLAB" if lado == "AGENDAMENTOS" else "AGENDAMENTOS"
        hs = {}
        for k in um:
            h1 = hist(lado, um, k)
            h2 = historico(outro, um[k][0], um[k][1]) if historico else None
            hs[k] = max([h for h in (h1, h2) if h is not None], default=None)
        if any(h for h in hs.values()):
            k = max(um, key=lambda k: hs[k] or 0)
            lote.notas.append(f"Identificação do paciente: “{col(um[k][2])}” — {hs[k]:.0f}% dos pacientes já "
                              "estão no banco (agenda ou Worklab importados antes)")
        else:   # sem histórico: prefere o ID do paciente
            k = next((k for k in um if k.startswith("ID:")), next(iter(um)))
        lote.identificacao = ROTULO_TIPO[um[k][0]]
        esc = (um[k][0], um[k][2])
        return (esc, None) if ca else (None, esc)

    pares = []
    for ka, (ta, sa, xa) in ca.items():
        for kw, (tw, sw, xw) in cw.items():
            if ta != tw:
                continue
            cruz = 100.0 * len(sa & sw) / len(sa)
            ha, hw = hist("AGENDAMENTOS", ca, ka), hist("WORKLAB", cw, kw)
            hs = [h for h in (ha, hw) if h is not None]
            nota = cruz + (sum(hs) / len(hs) if hs else 0)
            pares.append({"nota": nota, "cruz": cruz, "ha": ha, "hw": hw, "ka": ka, "kw": kw,
                          "col_a": col(ca[ka][2]), "col_w": col(cw[kw][2]), "id": ta == "codigo"})
    if not pares:
        lote.problemas.append("Agenda e Worklab não têm nenhuma identificação de paciente em comum "
                              "(ID, CPF, CNS ou nome + nascimento): o cruzamento não vai funcionar.")
        ka = next(iter(ca)); kw = next(iter(cw))
        return (ca[ka][0], ca[ka][2]), (cw[kw][0], cw[kw][2])
    lote.opcoes = sorted(pares, key=lambda p: -p["nota"])
    p = max(pares, key=lambda p: (round(p["nota"], 1), p["id"]))
    ka, kw = p["ka"], p["kw"]
    lote.identificacao, lote.cruzamento_pct = ROTULO_TIPO[ca[ka][0]], p["cruz"]
    extra = ""
    if p["ha"] is not None or p["hw"] is not None:
        extra = (f"; reconhecidos nos meses anteriores: agenda {p['ha'] or 0:.0f}%, Worklab {p['hw'] or 0:.0f}%")
    lote.notas.append(f"Identificação do paciente: {lote.identificacao} (agenda “{p['col_a']}” × Worklab “{p['col_w']}”) — "
                      f"{p['cruz']:.0f}% de quem compareceu aparece no Worklab{extra}")
    return (ca[ka][0], ca[ka][2]), (cw[kw][0], cw[kw][2])


def _canonizar(df: pd.DataFrame, fonte: str, ident) -> pd.DataFrame:
    """Monta a tabela final só com as colunas úteis + a identificação escolhida (as outras de paciente saem)."""
    from .importador import mapear_colunas
    tipo, cols = ident if ident else (None, None)
    pessoais = {"nome", "cpf", "cns", "nascimento", "codigo"}
    tmp = df.copy()
    if tipo == "codigo":      # a coluna escolhida passa a se chamar ID_Paciente (e a antiga, se houver, sai do caminho)
        tmp = tmp.rename(columns={c: f"__outra_{i}__" for i, c in enumerate(tmp.columns)
                                  if _n(c) == "id_paciente" and c != cols})
        tmp = tmp.rename(columns={cols: "ID_Paciente"})
    _, escolhidas = mapear_colunas(tmp, fonte)
    out = pd.DataFrame({CANONICO[c]: tmp[col].values for c, col in escolhidas.items()
                        if c in CANONICO and c not in pessoais})
    if tipo == "codigo":
        out["ID_Paciente"] = tmp["ID_Paciente"].values
    elif tipo == "cpf":
        out["CPF"] = df[cols].values
    elif tipo == "cns":
        out["CNS"] = df[cols].values
    elif tipo == "nome_nascimento":
        out["Paciente"], out["Data Nascimento"] = df[cols[0]].values, df[cols[1]].values
    return out


def montar(arquivos: list[tuple[str, bytes]], historico=None) -> Lote:
    lote = Lote([Arquivo(n, c) for n, c in arquivos])
    for a in lote.arquivos:
        try:
            a.df = ler_arquivo(a.conteudo, a.nome)
            a.papel = classificar(a.df)
            if a.papel in ("AGENDAMENTOS", "EXAMES"):          # relê escolhendo o cabeçalho para esse tipo
                a.df = ler_arquivo(a.conteudo, a.nome, "AGENDAMENTOS" if a.papel == "AGENDAMENTOS" else "WORKLAB")
        except ArquivoIlegivel as e:
            a.erro = str(e)
    por = {}
    for a in lote.arquivos:
        if a.df is not None and a.papel != "DESCONHECIDO":
            if a.papel in por:
                lote.problemas.append(f"Dois arquivos de {NOMES_PAPEL[a.papel]} ({por[a.papel].nome} e {a.nome}): "
                                      "envie um mês por vez.")
            por.setdefault(a.papel, a)
    uni = por["UNIDADES"].df if "UNIDADES" in por else None

    ag = por["AGENDAMENTOS"].df if "AGENDAMENTOS" in por else None
    if ag is not None:
        ag = _trocar_id_unidade(ag, uni, lote, "Agenda")
    wl = None
    if "EXAMES" in por:
        wl = _juntar_worklab(por["EXAMES"].df, por["ATENDIMENTOS"].df if "ATENDIMENTOS" in por else None, lote)
        wl = _trocar_id_unidade(wl, uni, lote, "Worklab")
    elif "ATENDIMENTOS" in por:
        lote.problemas.append("Veio o arquivo de atendimentos do Worklab sem o de exames (ex.: fExames_Setembro.csv). "
                              "Os dois precisam ir juntos.")

    id_ag, id_wl = _escolher_identificacao(ag, wl, lote, historico)
    try:
        if ag is not None:
            lote.agenda = _canonizar(ag, "AGENDAMENTOS", id_ag)
    except ColunasAusentes as e:
        lote.problemas.append(f"Agenda: {e}")
    try:
        if wl is not None:
            lote.worklab = _canonizar(wl, "WORKLAB", id_wl)
    except ColunasAusentes as e:
        lote.problemas.append(f"Worklab: {e}")
    return lote


def importar_lote(engine, arquivos: list[tuple[str, bytes]], *, segredo: str, usuario_id=None, criar_ubs=True,
                  hoje=None) -> tuple[Lote, list]:
    """Importa agenda e depois Worklab. Devolve (lote, [(fonte, Relatorio | Exception)])."""
    lote = montar(arquivos, historico_do_banco(engine, segredo))
    resultados = []
    partes = []
    if lote.agenda is not None:
        nomes = [a.nome for a in lote.arquivos if a.papel in ("AGENDAMENTOS", "UNIDADES")]
        partes.append(("AGENDAMENTOS", lote.agenda, nomes))
    if lote.worklab is not None:
        nomes = [a.nome for a in lote.arquivos if a.papel in ("EXAMES", "ATENDIMENTOS", "UNIDADES")]
        partes.append(("WORKLAB", lote.worklab, nomes))
    conteudo = {a.nome: a.conteudo for a in lote.arquivos}
    for fonte, df, nomes in partes:
        h = hashlib.sha256(b"".join(conteudo[n] for n in sorted(nomes)) + fonte.encode()).digest()
        try:
            rel = importar(engine, fonte, h, " + ".join(nomes)[:200], segredo=segredo, usuario_id=usuario_id,
                           criar_ubs=criar_ubs, estrategia=_estrategia(df), hoje=hoje, df=df.astype(object),
                           notas=lote.notas, min_freq_exame_novo=1)
            resultados.append((fonte, rel))
        except (ArquivoJaImportado, ColunasAusentes) as e:
            resultados.append((fonte, e))
    return lote, resultados


def _estrategia(df: pd.DataFrame) -> tuple:
    cols = set(df.columns)
    if "ID_Paciente" in cols:
        return ("codigo",)
    if "CPF" in cols:
        return ("cpf",)
    if "CNS" in cols:
        return ("cns",)
    return ("nome_nascimento",)
