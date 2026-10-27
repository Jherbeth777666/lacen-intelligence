"""Fixtures: banco de teste isolado (lacen_teste), recriado a cada sessão."""
import os
import subprocess
import sys
from datetime import date

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL_TESTE = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://postgres@/lacen_teste?host=/tmp")
os.environ["DATABASE_URL"] = URL_TESTE
os.environ["PSEUDO_SECRET"] = "segredo-de-teste"
os.environ["FLASK_SECRET_KEY"] = "chave-de-teste"
os.environ["ANTHROPIC_API_KEY"] = ""
sys.path.insert(0, RAIZ)

HOJE = date(2026, 10, 6)

AGEND_CSV = """Data;Horário;Unidade;Paciente;Data Nascimento;CNS;Tipo;Status;Observações
01/08/2026;07:00;UBS ALFA;MARIA SILVA SOUZA;01/01/1980;700000000000001;Exame de Rotina;Compareceu;gestante
01/08/2026;07:00;ubs alfa;JOAO PEREIRA LIMA;02/02/1970;700000000000002;Exame de Rotina;Faltou;
01/08/2026;07:00;UBS BETA;ANA COSTA ROCHA;03/03/1990;700000000000003;Exame de Rotina;Compareceu;
01/08/2026;07:00;UBS BETA;ANA COSTA ROCHA;03/03/1990;700000000000003;Exame de Rotina;Compareceu;
31/06/2026;07:00;UBS ALFA;PEDRO ALVES DIAS;04/04/1985;700000000000004;Exame de Rotina;Compareceu;
02/08/2026;07:00;UBS INEXISTENTE;LUCAS MOURA PINTO;05/05/1995;700000000000005;Exame de Rotina;Compareceu;
02/08/2026;07:30;U.B.S. Beta;CARLA DIAS NUNES;06/06/1966;700000000000006;Urgência;Talvez;
02/08/2026;07:00;UBS BETA;;;;Exame de Rotina;Compareceu;
"""

WL_CSV = """Atendimento;Data Atendimento;Nome Paciente;Data Nascimento;Unidade;Sigla Exame;Valor;Situação;Data Liberação
A1;01/08/2026;MARIA SILVA SOUZA;01/01/1980;;HEMO;4,11;Liberado;01/08/2026 15:00
A1;01/08/2026;MARIA SILVA SOUZA;01/01/1980;;GLI;1,85;Liberado;01/08/2026 16:00
A1;01/08/2026;MARIA SILVA SOUZA;01/01/1980;;GLI;1,85;Liberado;01/08/2026 16:00
A2;01/08/2026;ANA COSTA ROCHA;03/03/1990;UBS BETA;TSH;8,96;Recoleta;
A2;01/08/2026;OUTRA PESSOA X;03/03/1990;UBS BETA;HEMO;4,11;Liberado;
A3;01/08/2026;ZECA TESTE NOVO;01/01/2000;;CONFERIDO;;;
A4;99/99/2026;ZECA TESTE NOVO;01/01/2000;;HEMO;;;
"""


@pytest.fixture(scope="session")
def banco():
    import psycopg
    with psycopg.connect("dbname=postgres user=postgres host=/tmp", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS lacen_teste WITH (FORCE)")
        c.execute("CREATE DATABASE lacen_teste")
    subprocess.run([sys.executable, "-m", "scripts.migrar"], cwd=RAIZ, check=True, env=os.environ.copy(),
                   capture_output=True)
    from sqlalchemy import create_engine, text
    eng = create_engine(URL_TESTE, future=True)
    with eng.begin() as c:
        c.execute(text("INSERT INTO ubs (nome) VALUES ('UBS ALFA'), ('UBS BETA')"))
    from app.db import init_engine
    init_engine(URL_TESTE)
    return eng


@pytest.fixture(scope="session")
def carregado(banco):
    from etl.importador import importar
    ra = importar(banco, "AGENDAMENTOS", AGEND_CSV.encode(), "agend.csv", segredo="segredo-de-teste", hoje=HOJE)
    rw = importar(banco, "WORKLAB", WL_CSV.encode(), "wl.csv", segredo="segredo-de-teste", hoje=HOJE)
    return {"agend": ra, "wl": rw, "engine": banco}


@pytest.fixture(scope="session")
def app(carregado):
    import bcrypt
    from sqlalchemy import text
    from app import create_app
    from app.config import Config
    Config.DATABASE_URL = URL_TESTE
    Config.PSEUDO_SECRET = "segredo-de-teste"
    Config.ANTHROPIC_API_KEY = ""
    h = bcrypt.hashpw(b"Senha@Teste1", bcrypt.gensalt()).decode()
    with carregado["engine"].begin() as c:
        for perfil in ("admin", "gestor", "triagem", "qualidade"):
            c.execute(text("INSERT INTO usuarios (nome,email,senha_hash,perfil) VALUES (:n,:e,:h,:p)"),
                      {"n": perfil.title(), "e": f"{perfil}@t.br", "h": h, "p": perfil})
        c.execute(text("INSERT INTO profissionais (nome, registro) VALUES ('DR TESTE', '1')"))
    a = create_app(Config)
    a.config.update(TESTING=True)
    return a


@pytest.fixture
def cliente(app):
    return app.test_client()


def logar(cliente, perfil):
    cliente.get("/login")
    with cliente.session_transaction() as s:
        token = s.get("_csrf")
    r = cliente.post("/login", data={"email": f"{perfil}@t.br", "senha": "Senha@Teste1", "_csrf": token})
    with cliente.session_transaction() as s:
        return s.get("_csrf")


@pytest.fixture(autouse=True)
def _cache_limpo():
    """Cada teste começa sem resultados guardados (app/cache.py)."""
    from app import cache
    cache.limpar()
    yield
