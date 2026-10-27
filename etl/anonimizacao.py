"""Pseudonimização de pacientes.

O mesmo paciente precisa receber o MESMO código em todas as fontes
(agendamento, Worklab, triagem) para que o cruzamento funcione — e o código
não pode ser revertido para o dado original. Por isso usamos HMAC-SHA256
com um segredo guardado fora do repositório (PSEUDO_SECRET).

Ordem de preferência da chave (configurável):
  CPF válido  →  NOME + DATA DE NASCIMENTO  →  CNS  →  código de origem
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import date

from .normalizacao import cpf_valido, normalizar_texto, so_digitos

ESTRATEGIA_PADRAO = ("cpf", "nome_nascimento", "cns", "codigo")


class SegredoAusente(RuntimeError):
    pass


def pseudo_id(identificador: str, segredo: str) -> str:
    if not segredo:
        raise SegredoAusente("PSEUDO_SECRET não configurado — defina no .env antes de importar dados.")
    dig = hmac.new(segredo.encode(), identificador.encode(), hashlib.sha256).hexdigest()
    return "PAC_" + dig[:12].upper()


def chave_paciente(*, cpf=None, nome=None, nascimento: date | None = None, cns=None, codigo=None,
                   estrategia=ESTRATEGIA_PADRAO) -> tuple[str | None, str | None]:
    """Retorna (identificador_canonico, tipo_usado) ou (None, None) se não houver dado suficiente."""
    for tipo in estrategia:
        if tipo == "cpf":
            c = so_digitos(cpf)
            if cpf_valido(c):
                return f"CPF:{c}", "cpf"
        elif tipo == "nome_nascimento":
            n = normalizar_texto(nome)
            if n and nascimento and len(n.split()) >= 2:
                return f"NN:{n}|{nascimento.isoformat()}", "nome_nascimento"
        elif tipo == "cns":
            c = so_digitos(cns)
            if len(c) == 15:
                return f"CNS:{c}", "cns"
        elif tipo == "codigo":
            c = normalizar_texto(codigo)
            if c:
                return f"COD:{c}", "codigo"
    return None, None
