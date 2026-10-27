"""Testes da aplicação web: autenticação, permissões, CSRF, privacidade e fluxo de triagem."""
from .conftest import logar


def _token(cliente, url="/"):
    cliente.get(url)
    with cliente.session_transaction() as s:
        return s["_csrf"]


def test_rotas_exigem_login(cliente):
    r = cliente.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert cliente.post("/analista/api/perguntar", json={}).status_code in (400, 401)


def test_login_invalido(cliente):
    cliente.get("/login")
    with cliente.session_transaction() as s:
        t = s["_csrf"]
    r = cliente.post("/login", data={"email": "admin@t.br", "senha": "errada", "_csrf": t})
    assert r.status_code == 401


def test_post_sem_csrf_e_recusado(cliente):
    logar(cliente, "triagem")
    r = cliente.post("/triagem/nova", data={"nome": "X"})
    assert r.status_code == 400


def test_permissoes_por_perfil(cliente):
    logar(cliente, "triagem")
    assert cliente.get("/triagem/").status_code == 200
    assert cliente.get("/agendamentos/").status_code == 403
    assert cliente.get("/configuracoes/").status_code == 403


def test_todas_as_telas_do_gestor_carregam(cliente):
    logar(cliente, "admin")
    for url in ("/", "/triagem/", "/agendamentos/", "/laboratorio/", "/qualidade/", "/processos/",
                "/analista/", "/importacao/", "/configuracoes/", "/qualidade/nova", "/triagem/nova"):
        r = cliente.get(url)
        assert r.status_code == 200, url
        assert "Content-Security-Policy" in r.headers


def test_fluxo_de_triagem_e_mascaramento(cliente, carregado):
    from sqlalchemy import text
    logar(cliente, "triagem")
    t = _token(cliente, "/triagem/nova")
    with carregado["engine"].connect() as c:
        ubs = c.execute(text("SELECT id FROM ubs LIMIT 1")).scalar()
        exames = [r[0] for r in c.execute(text("SELECT id FROM exames WHERE sigla IN ('HEMO','GLI')"))]
    dados = {"_csrf": t, "nome": "Paciente Teste Silva", "cpf": "529.982.247-25", "data_nascimento": "1990-05-10",
             "sexo": "F", "ubs_id": str(ubs), "prioridade": "URGENTE", "exames": [str(e) for e in exames]}
    r = cliente.post("/triagem/nova", data=dados)
    assert r.status_code == 302
    with carregado["engine"].connect() as c:
        tid, pseudo = c.execute(text("""SELECT t.id, p.pseudo_id FROM triagens t JOIN pacientes p ON p.id=t.paciente_id
                                        WHERE p.cpf='52998224725'""")).one()
        assert c.execute(text("SELECT count(*) FROM triagem_exames WHERE triagem_id=:t"), {"t": tid}).scalar() == 2
        aud = c.execute(text("SELECT depois FROM auditoria WHERE tabela='pacientes' ORDER BY id DESC LIMIT 1")).scalar()
    assert "cpf" not in aud                   # auditoria não guarda CPF
    pagina = cliente.get(f"/triagem/{tid}").get_data(as_text=True)
    assert "***.982.247-**" in pagina and "52998224725" not in pagina
    t = _token(cliente, f"/triagem/{tid}")
    assert cliente.post(f"/triagem/{tid}/confirmar", data={"_csrf": t}).status_code == 302
    with carregado["engine"].connect() as c:
        assert c.execute(text("SELECT situacao FROM triagens WHERE id=:t"), {"t": tid}).scalar() == "CONFIRMADA"


def test_cpf_invalido_rejeitado(cliente):
    logar(cliente, "triagem")
    t = _token(cliente, "/triagem/nova")
    r = cliente.post("/triagem/nova", data={"_csrf": t, "nome": "Fulano de Tal", "cpf": "111.111.111-11",
                                            "data_nascimento": "1990-01-01", "sexo": "M", "ubs_id": "1",
                                            "prioridade": "ROTINA", "exames": ["1"]})
    assert r.status_code == 400 and "CPF inválido" in r.get_data(as_text=True)


def test_nc_exige_acao_para_concluir(cliente):
    logar(cliente, "qualidade")
    t = _token(cliente, "/qualidade/nova")
    base = {"_csrf": t, "data": "2026-09-01", "etapa": "ANALITICA", "categoria": "Falta de reagente",
            "problema": "Reagente de TSH esgotado no setor", "status": "CONCLUIDA"}
    assert cliente.post("/qualidade/nova", data=base).status_code == 400
    r = cliente.post("/qualidade/nova", data={**base, "acao_corretiva": "Compra emergencial e ponto de pedido"})
    assert r.status_code == 302


def test_api_analista(cliente):
    logar(cliente, "gestor")
    t = _token(cliente, "/analista/")
    r = cliente.post("/analista/api/perguntar", json={"pergunta": "Qual UBS merece maior atenção?"},
                     headers={"X-CSRF-Token": t})
    assert r.status_code == 200
    j = r.get_json()
    assert j["base"]["periodo"] and j["resposta"]


def test_periodo_todos_os_meses(cliente):
    logar(cliente, "gestor")
    for url in ("/", "/agendamentos/", "/laboratorio/", "/qualidade/", "/processos/"):
        r = cliente.get(url + "?mes=todos")
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert 'value="todos" selected' in html
        assert "Todos os meses (" in html
