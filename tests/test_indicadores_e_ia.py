from datetime import date

from app.services import indicadores as ind
from app.services.alertas import acima_da_referencia, diferenca_significativa
from app.services.indicadores import Periodo


def test_periodo_anterior_mes_cheio():
    p = Periodo(date(2026, 9, 1), date(2026, 9, 30))
    assert p.anterior() == Periodo(date(2026, 8, 1), date(2026, 8, 31))
    q = Periodo(date(2026, 9, 10), date(2026, 9, 19))
    assert q.anterior() == Periodo(date(2026, 8, 31), date(2026, 9, 9))


def test_taxa_de_falta_usa_apenas_realizados(carregado):
    k = ind.kpis(Periodo(date(2026, 8, 1), date(2026, 8, 31)))
    assert k["realizados"] == 3 and k["faltas"] == 1
    assert k["taxa_falta"] == 33.3
    assert k["taxa_comparecimento"] == 66.7


def test_testes_estatisticos():
    assert diferenca_significativa(60, 100, 20, 100)
    assert not diferenca_significativa(6, 20, 4, 20)       # amostra pequena: não alerta
    assert acima_da_referencia(50, 100, 0.25)
    assert not acima_da_referencia(28, 100, 0.25)


def test_analista_offline_nao_inventa(carregado):
    from app.services.analista import offline
    r = offline.responder("Qual a satisfação dos pacientes?")
    assert r["resposta"].startswith("Não há dados suficientes para concluir.")
    r = offline.responder("Como foram as faltas em janeiro de 2020?")
    assert "Não há dados suficientes" in r["resposta"]


def test_analista_offline_cita_numeros_do_banco(carregado):
    from app.services.analista import offline
    r = offline.responder("Me dê um resumo de agosto")
    assert "33,3%" in r["resposta"]          # taxa de falta calculada acima
    assert r["periodo"] == Periodo(date(2026, 8, 1), date(2026, 8, 31))


def test_analista_registra_consulta_e_base(carregado):
    from app.services.analista import perguntar
    r = perguntar("Me dê um resumo de agosto")
    assert r["modo"] == "OFFLINE"
    assert r["base"]["periodo"] == "01/08/2026 a 31/08/2026"
    assert r["base"]["registros"] > 0


def test_modo_llm_usa_ferramentas(carregado, monkeypatch):
    """Simula o Claude pedindo uma ferramenta e depois respondendo — sem rede."""
    import sys
    import types

    chamadas = []

    class Bloco:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Mensagens:
        def create(self, **kw):
            chamadas.append(kw)
            if len(chamadas) == 1:
                return Bloco(stop_reason="tool_use", content=[
                    Bloco(type="tool_use", id="t1", name="kpis_periodo", input={"inicio": "2026-08-01", "fim": "2026-08-31"})])
            resultado = kw["messages"][-1]["content"][0]["content"]
            assert '"taxa_falta": 33.3' in resultado      # o modelo recebe o número real
            return Bloco(stop_reason="end_turn", content=[Bloco(type="text", text="Taxa de falta de 33,3%.")])

    falso = types.SimpleNamespace(Anthropic=lambda api_key: types.SimpleNamespace(messages=Mensagens()))
    monkeypatch.setitem(sys.modules, "anthropic", falso)
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "chave-falsa")
    r = analista.perguntar("Como foi agosto?")
    assert r["modo"] == "LLM" and "33,3%" in r["resposta"]
    assert r["ferramentas"] == ["kpis_periodo"]
    assert r["base"]["periodo"] == "01/08/2026 a 31/08/2026"
    assert "nunca estime" in chamadas[0]["system"].lower() or "nunca" in chamadas[0]["system"].lower()


def test_llm_com_falha_cai_para_offline(carregado, monkeypatch):
    import sys
    import types

    def explode(api_key):
        raise ConnectionError("sem rede")
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=explode))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "chave-falsa")
    r = analista.perguntar("Resumo de agosto")
    assert r["modo"] == "OFFLINE" and r["aviso"]


def test_modo_gemini_usa_ferramentas(carregado, monkeypatch):
    """Simula o Gemini pedindo uma ferramenta e depois respondendo — sem rede."""
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")
    from google.genai import types as gt

    chamadas = []

    class Modelos:
        def generate_content(self, model, contents, config):
            chamadas.append({"model": model, "contents": list(contents), "config": config})
            if len(chamadas) == 1:
                ch = tp.SimpleNamespace(id="c1", name="kpis_periodo", args={"inicio": "2026-08-01", "fim": "2026-08-31"})
                return tp.SimpleNamespace(function_calls=[ch], text=None,
                                          candidates=[tp.SimpleNamespace(content=gt.Content(role="model", parts=[]))])
            resposta = contents[-1].parts[0].function_response
            assert resposta.name == "kpis_periodo" and resposta.id == "c1"
            assert resposta.response["resultado"]["dados"]["atual"]["taxa_falta"] == 33.3   # o modelo recebe o número real
            return tp.SimpleNamespace(function_calls=None, text="Taxa de falta de 33,3%.", candidates=[])

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    monkeypatch.setattr(Config, "GEMINI_MODEL", "gemini-teste")
    r = analista.perguntar("Como foi agosto?")
    assert r["modo"] == "LLM" and "33,3%" in r["resposta"]
    assert r["ferramentas"] == ["kpis_periodo"]
    assert r["base"]["periodo"] == "01/08/2026 a 31/08/2026"
    cfg = chamadas[0]["config"]
    assert chamadas[0]["model"] == "gemini-teste"
    assert "nunca" in cfg.system_instruction.lower()
    nomes = {d.name for d in cfg.tools[0].function_declarations}
    assert "kpis_periodo" in nomes and "fila_de_triagem" in nomes   # catálogo fechado inteiro, sem SQL


def test_gemini_sem_resposta_cai_para_offline(carregado, monkeypatch):
    """Resposta vazia (ex.: bloqueio de segurança) não pode virar 'não há dados' — cai para o modo offline com aviso."""
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")

    class Modelos:
        def generate_content(self, model, contents, config):
            return tp.SimpleNamespace(function_calls=None, text=None, prompt_feedback="SAFETY", candidates=[])

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    r = analista.perguntar("Resumo de agosto")
    assert r["modo"] == "OFFLINE" and r["aviso"]


def test_gemini_recebe_o_historico_da_conversa(carregado, monkeypatch):
    """Pergunta de continuação: as trocas anteriores vão antes da pergunta nova (memória da conversa)."""
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")
    vistos = []

    class Modelos:
        def generate_content(self, model, contents, config):
            vistos.append([(c.role, c.parts[0].text) for c in contents])
            return tp.SimpleNamespace(function_calls=None, text="Na UBS Alfa a taxa foi 50%.", candidates=[])

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    hist = [{"pergunta": "Como foi agosto?", "resposta": "Taxa de faltas de 33,3%."}, {"pergunta": 123, "resposta": "x"}]
    r = analista.perguntar("E na UBS Alfa?", historico=hist)
    assert r["modo"] == "LLM"
    assert vistos[0] == [("user", "Como foi agosto?"), ("model", "Taxa de faltas de 33,3%."), ("user", "E na UBS Alfa?")]


def test_grafico_da_resposta_vem_do_sistema(carregado):
    from app.services.analista import _grafico
    ago = Periodo(date(2026, 8, 1), date(2026, 8, 31))
    g = _grafico([{"ferramenta": "kpis_periodo"}, {"ferramenta": "evolucao_mensal"}], ago)
    assert g["tipo"] == "linha" and g["pct"] and g["series"][0]["valores"][g["rotulos"].index("2026-08")] == 33.3
    assert _grafico([{"ferramenta": "kpis_periodo"}], ago) is None
    j = _grafico([{"ferramenta": "jornada_do_paciente"}], ago)
    assert j["rotulos"][0] == "Agendados"


def test_relatorio_offline_nao_e_recusado(carregado):
    """'Destaques positivos' no pedido de relatório não pode cair na regra de 'resultado positivo de exame'."""
    from app.services.analista import PEDIDO_RELATORIO, offline
    r = offline.responder(PEDIDO_RELATORIO.format(periodo="agosto de 2026"))
    assert "Relatório executivo" in r["resposta"] and "não contém" not in r["resposta"]
    assert "jornada_do_paciente" in r["ferramentas"]



def test_ia_escolhe_o_grafico_e_o_sistema_monta(carregado, monkeypatch):
    """mostrar_grafico: o modelo pede o tipo; o gráfico vem do banco e volta na resposta."""
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")
    from google.genai import types as gt
    rodada = []

    class Modelos:
        def generate_content(self, model, contents, config):
            rodada.append(1)
            if len(rodada) == 1:
                ch = tp.SimpleNamespace(id="g1", name="mostrar_grafico",
                                        args={"tipo": "evolucao_faltas_mensal"})
                return tp.SimpleNamespace(function_calls=[ch], text=None,
                                          candidates=[tp.SimpleNamespace(content=gt.Content(role="model", parts=[]))])
            r = contents[-1].parts[0].function_response.response["resultado"]
            assert r["ok"] is True
            return tp.SimpleNamespace(function_calls=None, text="Veja a evolução no gráfico.", candidates=[])

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    r = analista.perguntar("Mostre a evolução das faltas")
    assert len(r["graficos"]) == 1 and r["graficos"][0]["tipo"] == "linha"
    assert "mostrar_grafico" not in r["ferramentas"]


def test_novas_ferramentas_da_ia(carregado):
    from app.services.analista import ferramentas as f
    assert f.executar("detalhe_exame", {"exame": "hemo", "inicio": "2026-08-01", "fim": "2026-08-31"})["dados"]["sigla"] == "HEMO"
    c = f.executar("comparar_periodos", {"inicio_a": "2026-07-01", "fim_a": "2026-07-31",
                                         "inicio_b": "2026-08-01", "fim_b": "2026-08-31"})
    assert c["rede_b"]["faltas"] == 1 and "rede_a" in c
    assert "dias" in f.executar("serie_diaria", {"inicio": "2026-08-01", "fim": "2026-08-31"})["dados"]



def test_motivo_da_falha_e_nova_tentativa(monkeypatch):
    from app.services.analista import llm
    erro = type("ClientError", (Exception,), {})("429 RESOURCE_EXHAUSTED")
    erro.code = 429
    assert "limite de uso" in llm.motivo_legivel(erro)
    chave = type("ClientError", (Exception,), {})("API key not valid"); chave.code = 400
    assert "chave" in llm.motivo_legivel(chave)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    tentativas = []
    def chamar():
        tentativas.append(1)
        if len(tentativas) < 3:
            raise erro
        return "ok"
    assert llm._com_nova_tentativa(chamar) == "ok" and len(tentativas) == 3


def test_modelo_sobrecarregado_troca_para_reserva(carregado, monkeypatch):
    """503 'high demand' no modelo principal: descobre os modelos da conta e responde com um reserva."""
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")
    from app.services.analista import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_reservas_descobertas", None)
    usados = []

    def erro(cod, msg):
        e = type("ServerError", (Exception,), {})(f"{cod} {msg}"); e.code = cod
        return e

    class Modelos:
        def list(self):
            return [tp.SimpleNamespace(name=f"models/{n}", supported_actions=a) for n, a in (
                ("gemini-3.8-flash", ["generateContent"]), ("gemini-3.8-flash-lite", ["generateContent"]),
                ("gemini-2.5-flash", ["generateContent"]), ("gemini-3.8-flash-image", ["generateContent"]),
                ("text-embedding-004", ["embedContent"]))]

        def generate_content(self, model, contents, config):
            usados.append(model)
            if model == "gemini-3.8-flash":
                raise erro(503, "UNAVAILABLE high demand")
            if model == "gemini-3.8-flash-lite":
                raise erro(404, "NOT_FOUND no longer available")
            return tp.SimpleNamespace(function_calls=None, text="Resposta do reserva.", candidates=[])

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    monkeypatch.setattr(Config, "GEMINI_MODEL", "gemini-3.8-flash")
    r = analista.perguntar("Como foi agosto?")
    assert r["modo"] == "LLM" and r["modelo"] == "gemini-2.5-flash" and not r["aviso"]
    # sem repetir o modelo sobrecarregado: troca na hora (cota grátis é por modelo)
    assert usados == ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-2.5-flash"]
    assert "sobrecarregados" in llm.motivo_legivel(erro(503, "UNAVAILABLE"))


def test_ordem_dos_modelos_reserva_intercala_lite(monkeypatch):
    import types as tp
    from app.services.analista import llm
    monkeypatch.setattr(llm, "_reservas_descobertas", None)
    nomes = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
             "gemini-3.6-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash-image"]
    cli = tp.SimpleNamespace(models=tp.SimpleNamespace(list=lambda: [
        tp.SimpleNamespace(name=f"models/{n}", supported_actions=["generateContent"]) for n in nomes]))
    assert llm._modelos_reserva(cli, "gemini-3.8-flash") == [
        "gemini-3.6-flash-lite", "gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash"]


def test_sugestoes_da_ia_saem_do_texto_e_vao_para_os_botoes(carregado, monkeypatch):
    import types as tp

    import pytest
    genai = pytest.importorskip("google.genai")

    class Modelos:
        def generate_content(self, model, contents, config):
            assert "TODO o período" in config.system_instruction and "Período padrão" not in config.system_instruction
            return tp.SimpleNamespace(function_calls=None, candidates=[],
                                      text="A UBS Alfa teve mais presenças.\n\nSUGESTOES: E na UBS Beta? | Compare com julho | Quais exames?")

    monkeypatch.setattr(genai, "Client", lambda api_key: tp.SimpleNamespace(models=Modelos()))
    from app.config import Config
    from app.services import analista
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "chave-falsa")
    r = analista.perguntar("Qual UBS teve mais presenças?")
    assert r["resposta"] == "A UBS Alfa teve mais presenças."
    assert r["sugestoes"] == ["E na UBS Beta?", "Compare com julho", "Quais exames?"]


def test_offline_entende_todo_o_periodo_e_intervalo(carregado):
    from app.services.analista.offline import extrair_periodo
    t = extrair_periodo("COMO FOI EM TODO O PERIODO?")
    assert t.todos
    r = extrair_periodo("FALTAS DE JULHO A AGOSTO")
    assert (r.inicio, r.fim) == (date(2026, 7, 1), date(2026, 8, 31))


def test_grafico_de_presencas_por_ubs(carregado):
    from app.services.analista.graficos import montar
    g = montar("ranking_presencas_ubs", Periodo(date(2026, 6, 1), date(2026, 8, 31)))
    assert g and not g["pct"] and g["series"][0]["valores"] == sorted(g["series"][0]["valores"], reverse=True)


def test_offline_responde_presencas_por_numero(carregado):
    from app.services.analista import offline
    r = offline.responder("Quais UBS tiveram mais presenças em todo o período?")
    assert "mais presenças" in r["resposta"] and "presenças (" in r["resposta"]
