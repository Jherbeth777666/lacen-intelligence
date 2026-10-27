"""
Módulo de importação e validação de dados (ETL).
Processa relatórios de agendamentos e exportações do WorkLab.
"""
from __future__ import annotations

import io
import re
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection

# -----------------------------------------------------------------------------
# Dicionários de Mapeamento, Sinónimos e Regras
# -----------------------------------------------------------------------------

COLUNAS = {
    "agendamentos": {
        "horario": ["horario", "horário", "hora"],
        "paciente": ["paciente", "nome", "nome_paciente"],
        "tipo": ["tipo", "tipo_agendamento", "tipo_de_atendimento"],
        "unidade": ["unidade", "ubs", "posto", "origem", "posto_saude"],
        "status": ["status", "situacao", "comparecimento"],
        "observacoes": ["observacoes", "observações", "observacao", "obs"],
        "data": ["data", "data_agendamento", "dia"],
    },
    "worklab": {
        "atendimento": ["atendimento", "id_atendimento", "codigo_atendimento", "n_atendimento", "requisicao", "os", "num_os"],
        "paciente": ["paciente", "nome", "nome_paciente"],
        "data": ["data", "data_atendimento", "data_entrada"],
        "unidade": ["unidade", "convenio", "posto", "origem", "ubs"],
        "exame": ["exame", "exames", "procedimento", "descricao_exame"],
        "status": ["status", "situacao", "fase"],
    }
}

OBRIGATORIAS = {
    "agendamentos": ["paciente", "unidade", "data"],
    "worklab": ["atendimento", "paciente", "data"]
}

STATUS_AGEND = {
    "COMPARECEU": "COMPARECEU",
    "PRESENTE": "COMPARECEU",
    "ATENDIDO": "COMPARECEU",
    "REALIZADO": "COMPARECEU",
    "SIM": "COMPARECEU",
    "FALTOU": "FALTOU",
    "FALTA": "FALTOU",
    "AUSENTE": "FALTOU",
    "NAO COMPARECEU": "FALTOU",
    "NÃO COMPARECEU": "FALTOU",
    "NAO": "FALTOU",
    "NÃO": "FALTOU",
    "PENDENTE": "PENDENTE",
    "AGUARDANDO": "PENDENTE",
    "AGENDADO": "AGENDADO",
    "MARCADO": "AGENDADO",
    "CONFIRMADO": "AGENDADO",
}

TIPO_AGEND = {
    "EXAME DE ROTINA": "ROTINA",
    "ROTINA": "ROTINA",
    "ELETIVO": "ROTINA",
    "URGENCIA": "URGENCIA",
    "URGÊNCIA": "URGENCIA",
    "URGENTE": "URGENCIA",
    "PRIORITARIO": "URGENCIA",
    "PRIORITÁRIO": "URGENCIA",
}

SITUACAO_EXAME = {
    "LIBERADO": "LIBERADO",
    "CONCLUIDO": "LIBERADO",
    "CONCLUÍDO": "LIBERADO",
    "FINALIZADO": "LIBERADO",
    "PENDENTE": "PENDENTE",
    "EM ANDAMENTO": "EM ANDAMENTO",
}


# -----------------------------------------------------------------------------
# Exceções Customizadas
# -----------------------------------------------------------------------------

class ErroImportacao(Exception):
    pass


class ColunasAusentes(ErroImportacao):
    pass


class ArquivoJaImportado(ErroImportacao):
    def __init__(self, importacao_id: int):
        self.importacao_id = importacao_id
        super().__init__(f"Arquivo já importado anteriormente (ID de importação: {importacao_id})")


# -----------------------------------------------------------------------------
# Estrutura do Relatório
# -----------------------------------------------------------------------------

@dataclass
class RelatorioImportacao:
    fonte: str
    arquivo: str
    total_linhas: int = 0
    linhas_importadas: int = 0
    linhas_ignoradas: int = 0
    linhas_com_erro: int = 0
    novas_ubs_criadas: int = 0
    novos_exames_criados: int = 0
    colunas_reconhecidas: List[str] = field(default_factory=list)
    erros: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def sucesso(self) -> bool:
        return self.linhas_com_erro == 0

    def resumo(self) -> Dict[str, Any]:
        return {
            "fonte": self.fonte,
            "arquivo": self.arquivo,
            "total_linhas": self.total_linhas,
            "linhas_importadas": self.linhas_importadas,
            "linhas_ignoradas": self.linhas_ignoradas,
            "linhas_com_erro": self.linhas_com_erro,
            "novas_ubs": self.novas_ubs_criadas,
            "novos_exames": self.novos_exames_criados,
        }


# -----------------------------------------------------------------------------
# Funções Auxiliares de Tratamento
# -----------------------------------------------------------------------------

def normalizar_cabecalho(texto: Any) -> str:
    if pd.isna(texto) or texto is None:
        return ""
    txt = str(texto).strip().lower()
    txt = re.sub(r"[áàãâä]", "a", txt)
    txt = re.sub(r"[éèêë]", "e", txt)
    txt = re.sub(r"[íìîï]", "i", txt)
    txt = re.sub(r"[óòõôö]", "o", txt)
    txt = re.sub(r"[úùûü]", "u", txt)
    txt = re.sub(r"[ç]", "c", txt)
    txt = re.sub(r"\s+", "_", txt)
    return txt


def normalizar_texto(texto: Any) -> str:
    if pd.isna(texto) or texto is None:
        return ""
    return str(texto).strip()


def extrair_data_de_texto(texto: str) -> Optional[str]:
    if not texto:
        return None
    # YYYY-MM-DD ou YYYY_MM_DD
    m_iso = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", texto)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    # DD-MM-YYYY ou DD/MM/YYYY
    m_br = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", texto)
    if m_br:
        return f"{m_br.group(3)}-{m_br.group(2)}-{m_br.group(1)}"
    return None


def ler_arquivo(conteudo: bytes, nome_arquivo: str) -> pd.DataFrame:
    ext = Path(nome_arquivo).suffix.lower()
    buffer = io.BytesIO(conteudo)
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(buffer)
    
    for encoding in ["utf-8", "latin1", "cp1252"]:
        buffer.seek(0)
        for sep in [",", ";", "\t"]:
            try:
                df = pd.read_csv(buffer, sep=sep, encoding=encoding, dtype=object, keep_default_na=False)
                if len(df.columns) > 1:
                    return df
            except Exception:
                continue
    buffer.seek(0)
    return pd.read_csv(buffer, dtype=object, keep_default_na=False)


def mapear_colunas(df: pd.DataFrame, fonte: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    normal = {normalizar_cabecalho(c): c for c in df.columns}
    escolhidas = {}
    config_fonte = COLUNAS.get(fonte, {})
    for campo, sinonimos in config_fonte.items():
        for s in sinonimos:
            if s in normal:
                escolhidas[campo] = normal[s]
                break

    faltando = [c for c in OBRIGATORIAS.get(fonte, []) if c not in escolhidas]
    if faltando:
        raise ColunasAusentes(
            "Colunas obrigatórias não encontradas: " + ", ".join(faltando)
            + f". Colunas do arquivo: {list(df.columns)}"
        )

    saida = pd.DataFrame({campo: df[col] for campo, col in escolhidas.items()})
    saida["_linha"] = range(2, len(df) + 2)
    return saida, escolhidas


# -----------------------------------------------------------------------------
# Resolução de UBS e Exames via SQL Nativo
# -----------------------------------------------------------------------------

def obter_ou_criar_ubs(conn: Connection, nome_ubs: str, criar_ubs: bool = False) -> Optional[int]:
    nome_limpo = normalizar_texto(nome_ubs).upper()
    if not nome_limpo:
        return None

    res = conn.execute(
        text("SELECT id FROM ubs WHERE UPPER(nome) = :n"),
        {"n": nome_limpo}
    ).scalar()
    if res:
        return res

    res_alias = conn.execute(
        text("SELECT ubs_id FROM ubs_aliases WHERE UPPER(alias) = :n"),
        {"n": nome_limpo}
    ).scalar()
    if res_alias:
        return res_alias

    if criar_ubs:
        tipo = "UBS"
        if "CTA" in nome_limpo:
            tipo = "CTA"
        elif "ESPAÇO" in nome_limpo or "ESPACO" in nome_limpo:
            tipo = "ESPACO"
        elif "CENTRO" in nome_limpo:
            tipo = "CENTRO_ESPECIALIDADES"
        elif "PRISIONAL" in nome_limpo or "PRESIDIO" in nome_limpo:
            tipo = "PRISIONAL"

        novo_id = conn.execute(
            text("INSERT INTO ubs (nome, tipo, ativo) VALUES (:n, :t, true) RETURNING id"),
            {"n": nome_limpo, "t": tipo}
        ).scalar()
        return novo_id

    return None


# -----------------------------------------------------------------------------
# Função Principal de Importação
# -----------------------------------------------------------------------------

def importar(
    engine: Engine,
    fonte: str,
    conteudo: bytes,
    nome_arquivo: str,
    segredo: Optional[str] = None,
    criar_ubs: bool = False,
    df: Optional[pd.DataFrame] = None
) -> RelatorioImportacao:
    rel = RelatorioImportacao(fonte=fonte, arquivo=nome_arquivo)

    # 1. Validação de Hash para evitar duplicidades
    h = hashlib.sha256(conteudo).hexdigest()
    with engine.connect() as c:
        existente = c.execute(
            text("SELECT id FROM importacoes WHERE hash_arquivo = :h AND status <> 'FALHOU'"),
            {"h": h}
        ).scalar()
        if existente:
            raise ArquivoJaImportado(existente)

    # 2. Carregamento dos dados
    bruto = df if df is not None else ler_arquivo(conteudo, nome_arquivo)
    bruto = bruto.dropna(how="all")

    # Injeção da data extraída do nome do ficheiro (ex: relatorio-2026-07-01.xlsx)
    if fonte == "agendamentos":
        cols_atuais = [normalizar_cabecalho(col) for col in bruto.columns]
        if "data" not in cols_atuais and "dia" not in cols_atuais:
            data_nome = extrair_data_de_texto(nome_arquivo)
            if data_nome:
                bruto["data"] = data_nome

    # 3. Mapeamento de colunas
    tabela, rel.colunas_reconhecidas = mapear_colunas(bruto, fonte)
    rel.total_linhas = len(tabela)

    # 4. Transação no Banco de Dados
    with engine.begin() as conn:
        imp_id = conn.execute(
            text("""
                INSERT INTO importacoes (fonte, nome_arquivo, hash_arquivo, total_registros, registros_sucesso, registros_erro, status)
                VALUES (:fonte, :nome, :hash, :tot, 0, 0, 'PROCESSANDO')
                RETURNING id
            """),
            {"fonte": fonte, "nome": nome_arquivo, "hash": h, "tot": rel.total_linhas}
        ).scalar()

        for _, row in tabela.iterrows():
            linha_idx = row.get("_linha", 0)
            try:
                paciente = normalizar_texto(row.get("paciente"))
                if not paciente:
                    rel.linhas_ignoradas += 1
                    continue

                ubs_bruta = normalizar_texto(row.get("unidade", ""))
                ubs_id = obter_ou_criar_ubs(conn, ubs_bruta, criar_ubs=criar_ubs)
                if not ubs_id and criar_ubs and ubs_bruta:
                    rel.novas_ubs_criadas += 1

                val_data = str(row.get("data", "")).strip()
                data_final = extrair_data_de_texto(val_data) or extrair_data_de_texto(nome_arquivo)
                if not data_final:
                    data_final = date.today().isoformat()

                if fonte == "agendamentos":
                    horario = normalizar_texto(row.get("horario", "07:00"))
                    status_raw = normalizar_texto(row.get("status", "COMPARECEU")).upper()
                    status_padrao = STATUS_AGEND.get(status_raw, "COMPARECEU")
                    tipo_raw = normalizar_texto(row.get("tipo", "ROTINA")).upper()
                    tipo_padrao = TIPO_AGEND.get(tipo_raw, "ROTINA")
                    obs = normalizar_texto(row.get("observacoes", ""))

                    conn.execute(
                        text("""
                            INSERT INTO agendamentos (
                                importacao_id, ubs_id, ubs_nome_original,
                                paciente_nome, data_agendamento, horario,
                                tipo, status, observacoes
                            ) VALUES (
                                :imp, :ubs, :ubs_orig, :pac, :dt, :hr, :tp, :st, :obs
                            )
                        """),
                        {
                            "imp": imp_id, "ubs": ubs_id, "ubs_orig": ubs_bruta,
                            "pac": paciente, "dt": data_final, "hr": horario,
                            "tp": tipo_padrao, "st": status_padrao, "obs": obs
                        }
                    )
                    rel.linhas_importadas += 1

                elif fonte == "worklab":
                    atend_id = normalizar_texto(row.get("atendimento"))
                    exame_nome = normalizar_texto(row.get("exame"))

                    atend_db_id = conn.execute(
                        text("SELECT id FROM atendimentos WHERE numero_requisicao = :req"),
                        {"req": atend_id}
                    ).scalar()

                    if not atend_db_id:
                        atend_db_id = conn.execute(
                            text("""
                                INSERT INTO atendimentos (importacao_id, numero_requisicao, paciente_nome, data_atendimento, ubs_id)
                                VALUES (:imp, :req, :pac, :dt, :ubs)
                                RETURNING id
                            """),
                            {"imp": imp_id, "req": atend_id, "pac": paciente, "dt": data_final, "ubs": ubs_id}
                        ).scalar()

                    if exame_nome:
                        conn.execute(
                            text("""
                                INSERT INTO atendimentos_exames (atendimento_id, exame_nome_original)
                                VALUES (:atend, :ex)
                            """),
                            {"atend": atend_db_id, "ex": exame_nome}
                        )

                    rel.linhas_importadas += 1

            except Exception as e:
                rel.linhas_com_erro += 1
                rel.erros.append({"linha": linha_idx, "mensagem": str(e)})
                conn.execute(
                    text("""
                        INSERT INTO importacao_erros (importacao_id, linha, mensagem, dados_linha)
                        VALUES (:imp, :ln, :msg, :dados)
                    """),
                    {"imp": imp_id, "ln": linha_idx, "msg": str(e), "dados": str(dict(row))}
                )

        conn.execute(
            text("""
                UPDATE importacoes
                SET registros_sucesso = :suc, registros_erro = :err, status = :st
                WHERE id = :id
            """),
            {
                "suc": rel.linhas_importadas,
                "err": rel.linhas_com_erro,
                "st": "SUCESSO" if rel.linhas_com_erro == 0 else "CONCLUIDO_COM_ERROS",
                "id": imp_id
            }
        )

    return rel