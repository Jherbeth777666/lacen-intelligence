"""Funções puras de limpeza e padronização (fáceis de testar)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta

import pandas as pd

_ESPACOS = re.compile(r"\s+")


def normalizar_texto(valor) -> str:
    """'  U.B.S. Vila  Esperança ' -> 'UBS VILA ESPERANCA'"""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    s = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode()
    s = s.upper().replace(".", "")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return _ESPACOS.sub(" ", s).strip()


def normalizar_cabecalho(valor) -> str:
    return normalizar_texto(valor).lower().replace(" ", "_")


def so_digitos(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    s = str(valor)
    if s.endswith(".0"):  # números lidos como float pelo Excel
        s = s[:-2]
    return re.sub(r"\D", "", s)


def cpf_valido(cpf: str) -> bool:
    cpf = so_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(cpf[i]) * ((n + 1) - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(cpf[n]):
            return False
    return True


_FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d", "%d.%m.%Y")


def parse_data(valor) -> date | None:
    """Aceita date/datetime, texto em vários formatos e número serial do Excel."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor is pd.NaT:
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.date()
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)) and 20000 < float(valor) < 80000:  # serial Excel
        return (datetime(1899, 12, 30) + timedelta(days=float(valor))).date()
    s = str(valor).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}(\.0+)?", s):                       # serial do Excel que veio como texto
        return parse_data(float(s))
    m = re.match(r"^(\S+?)[ T]+\d{1,2}:\d{2}", s)             # "01/09/2026 07:30", "2026-09-01T07:30:00"
    if m:
        s = m.group(1)
    for fmt in _FORMATOS_DATA:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_hora(valor) -> time | None:
    """'07:00', '7h', 0.2916 (fração do dia do Excel), datetime -> time (arredondado ao minuto)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (datetime, pd.Timestamp)):
        t = valor.time()
    elif isinstance(valor, time):
        t = valor
    elif isinstance(valor, (int, float)) and 0 <= float(valor) < 1:
        minutos = round(float(valor) * 24 * 60)
        t = time(minutos // 60 % 24, minutos % 60)
    else:
        m = re.match(r"^\s*(\d{1,2})\s*[:hH]\s*(\d{0,2})(?::(\d{1,2}))?", str(valor))
        if not m:
            return None
        h, mi, se = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
        if h > 23 or mi > 59 or se > 59:
            return None
        t = time(h, mi, se)
    # remove ruído de ponto flutuante (ex.: 07:59:59.999 -> 08:00)
    total = t.hour * 60 + t.minute + (1 if t.second >= 30 else 0)
    return time(total // 60 % 24, total % 60)


def mascarar(valor: str, visiveis: int = 3) -> str:
    """Mascara dado pessoal para logs: 'MARIA DA SILVA' -> 'MAR***'."""
    s = str(valor or "")
    return (s[:visiveis] + "***") if s else ""


def mapear_valor(valor, mapa: dict[str, str]) -> str | None:
    """Mapeia texto livre para um domínio controlado usando chaves normalizadas."""
    chave = normalizar_texto(valor)
    if not chave:
        return None
    if chave in mapa:
        return mapa[chave]
    for k, v in mapa.items():  # aceita prefixo: 'FALTOU SEM AVISO' -> FALTOU
        if chave.startswith(k):
            return v
    return None
