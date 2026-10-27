"""Modo IA generativa do Analista: o modelo usa as ferramentas do catálogo fechado.

O modelo recebe a pergunta, decide quais indicadores consultar, recebe os
números do banco e escreve a análise. Ele não tem acesso a SQL, tabelas ou
dados pessoais — só às ferramentas de ferramentas.py.

Provedores suportados (escolhidos em app/config.py):
  • Gemini (Google)   — GEMINI_API_KEY
  • Claude (Anthropic) — ANTHROPIC_API_KEY
"""
from __future__ import annotations

import json
import os
import re
import time

from app.services import indicadores as ind

from . import ferramentas as fer

MAX_RODADAS = 10

SISTEMA = """Você é o Analista de Dados e Gestão do LACEN (Laboratório Central), apoiando gestores na tomada de decisão.

REGRAS INEGOCIÁVEIS
1. Use SOMENTE números retornados pelas ferramentas. Nunca estime, arredonde de cabeça ou invente números, UBS, exames, pacientes, causas ou tendências.
2. Se as ferramentas não trouxerem dados suficientes para responder, diga exatamente: "Não há dados suficientes para concluir." e explique qual dado faltaria.
3. Causas são HIPÓTESES. Só afirme como fato o que os dados mostram; escreva "possível causa (a verificar)" para o resto.
4. Uma variação só é "tendência" se aparecer em mais de um período ou for marcada como significativa pelos alertas.
5. Sempre informe o período analisado. Nunca peça nem mencione dados pessoais.
6. Responda em português do Brasil, de forma objetiva, para um gestor ocupado.

FORMATO
- Comece com a resposta direta (1–2 frases).
- Depois, evidências em lista curta, citando os números.
- Quando a pergunta pedir priorização, recomendação ou "o que fazer", estruture cada item assim:
  **Problema** → **Evidência** → **Possível causa (a verificar)** → **Ação recomendada** → **Indicador para acompanhar**
- Use markdown simples (negrito, listas, tabelas pequenas). Sem títulos enormes.
- GRÁFICOS: quando ver ajudar (ranking, evolução, dia a dia, comparação), chame mostrar_grafico (até 2). O sistema
  desenha com dados do banco; no texto, comente o que o gráfico mostra em vez de listar todos os valores.
- Ferramentas úteis: serie_diaria (dia a dia), comparar_periodos (dois meses lado a lado), detalhe_exame (um exame),
  faltas_ubs_por_dia_semana (UBS × dia), destaques_ubs (melhores e que mais melhoraram), exames_por_ubs.
- Em perguntas de continuação ("e na UBS X?", "compare com o mês anterior"), use o contexto da conversa,
  mas consulte as ferramentas de novo: os números da resposta sempre vêm das ferramentas.
- Se pedirem um RELATÓRIO, use as seções: **Resumo**, **Principais números**, **Pontos de atenção**
  (no formato Problema → Evidência → Possível causa → Ação → Indicador), **Destaques positivos** e **Próximos passos**.
- "Presenças" = número de pessoas que compareceram (volume); "comparecimento" = percentual. Quando perguntarem
  qual UBS "mais veio" ou "teve mais presenças", ordene pelo NÚMERO (campo compareceram), não pelo percentual.
- ÚLTIMA LINHA, sempre: `SUGESTOES: pergunta 1 | pergunta 2 | pergunta 3` — três perguntas curtas (até 70
  caracteres) de continuação, específicas para o que você acabou de encontrar (cite a UBS, o exame ou o mês).

PERÍODO
- Dados disponíveis: {meses} (de {inicio} a {fim}). Mês mais recente: {ultimo}; mês anterior a ele: {anterior}.
- Se o usuário citar o período ("em agosto", "de julho a setembro", "no último trimestre"), use exatamente esse.
- Se NÃO citar: perguntas sobre "agora", "este mês", "o que mudou", "mês atual" → {ultimo} comparado com {anterior};
  rankings, padrões, perfis, evolução e perguntas gerais → TODO o período com dados ({inicio} a {fim}).
- Passe SEMPRE inicio e fim nas ferramentas e diga na resposta qual período analisou.

DEFINIÇÕES
- Taxa de falta = faltas ÷ (compareceram + faltaram); TAT = liberação − coleta (07:00 do dia do atendimento).
"""

SEM_CONCLUSAO = ("Não consegui concluir a análise dentro do limite de consultas. "
                 "Tente uma pergunta mais específica.")


_SUGESTOES = re.compile(r"^[ \t>*_`-]*SUGEST(?:Õ|O)ES[*_`\s]*:\s*(.+?)\s*$", re.I | re.M)


def separar_sugestoes(texto: str) -> tuple[str, list[str]]:
    """Tira a linha 'SUGESTOES: a | b | c' da resposta e devolve (texto, [até 3 sugestões])."""
    achados = list(_SUGESTOES.finditer(texto or ""))
    if not achados:
        return texto, []
    m = achados[-1]
    itens = [x.strip(" -•*`\"'“”") for x in m.group(1).split("|")]
    itens = [x for x in itens if 3 < len(x) <= 120][:3]
    return (texto[:m.start()] + texto[m.end():]).strip(), itens


def _sistema() -> str:
    ult, tot = ind.periodo_padrao(), ind.periodo_total()
    return SISTEMA.format(ultimo=f"{ind.rotulo_natural(ult)} ({ult.rotulo()})",
                          anterior=f"{ind.rotulo_natural(ult.anterior())} ({ult.anterior().rotulo()})",
                          inicio=f"{tot.inicio:%Y-%m-%d}", fim=f"{tot.fim:%Y-%m-%d}",
                          meses=", ".join(ind.meses_disponiveis()) or "nenhum")


class _Registro:
    """Executa ferramentas pedidas pelo modelo e guarda o que foi usado (governança)."""

    def __init__(self):
        self.usadas: list[dict] = []
        self.periodos: set[tuple[str, str]] = set()
        self.graficos: list[dict] = []

    def _grafico(self, args: dict) -> dict:
        """mostrar_grafico: o modelo escolhe o tipo; o sistema monta com dados do banco."""
        from . import graficos
        if len(self.graficos) >= 2:
            return {"erro": "Limite de 2 gráficos por resposta já atingido."}
        ubs = fer.localizar_ubs(args.get("ubs")) if args.get("ubs") else None
        if args.get("ubs") and not ubs:
            return {"erro": f"UBS '{args['ubs']}' não encontrada."}
        g = graficos.montar(args.get("tipo", ""), fer.periodo_de(args), ubs, args.get("exame"))
        if not g:
            return {"erro": "Não há dados para esse gráfico no período (ou tipo desconhecido)."}
        self.graficos.append(g)
        return {"ok": True, "grafico": g["titulo"], "aviso": "O gráfico aparece abaixo da sua resposta; não repita os números dele em lista."}

    def executar(self, nome: str, args: dict | None) -> dict:
        args = dict(args or {})
        if nome == "mostrar_grafico":
            self.usadas.append({"ferramenta": nome, "parametros": args})
            return self._grafico(args)
        if args.get("inicio") or args.get("fim") or "inicio" in fer.FERRAMENTAS.get(nome, {}).get("parametros", {}):
            p = fer.periodo_de(args)
            self.periodos.add((p.inicio.isoformat(), p.fim.isoformat()))
        try:
            saida = fer.executar(nome, args)
        except Exception as e:  # erro de ferramenta vira informação para o modelo, não quebra a resposta
            saida = {"erro": f"Falha ao calcular: {type(e).__name__}"}
        self.usadas.append({"ferramenta": nome, "parametros": args})
        # ida e volta por JSON: datas e Decimals viram texto/número simples para qualquer provedor
        texto = json.dumps(saida, ensure_ascii=False, default=str)
        if len(texto) > 60000:
            return {"erro": "Resultado grande demais para analisar de uma vez; use um período ou uma UBS específica."}
        return json.loads(texto)

    def resultado(self, texto: str) -> dict:
        texto, sugestoes = separar_sugestoes((texto or "").strip())
        return {"resposta": texto or "Não há dados suficientes para concluir.", "sugestoes": sugestoes,
                "ferramentas": self.usadas, "periodos": sorted(self.periodos), "graficos": self.graficos}


def _codigo(e: Exception) -> int | None:
    c = getattr(e, "code", None) or getattr(e, "status_code", None)
    return c if isinstance(c, int) else None


def _com_nova_tentativa(chamar, esperas=(3, 8)):
    """Limite por minuto (429) ou instabilidade (500/503): espera um pouco e tenta de novo, até 2 vezes."""
    for espera in (*esperas, None):
        try:
            return chamar()
        except Exception as e:
            cod = _codigo(e)
            if espera is None or cod not in (429, 500, 502, 503, 504):
                raise
            time.sleep(espera)


def motivo_legivel(e: Exception) -> str:
    """Explica a falha da IA em português, sem expor detalhes técnicos sensíveis (como a chave)."""
    cod, txt = _codigo(e), str(e).upper()
    if cod == 429 or "RESOURCE_EXHAUSTED" in txt or "QUOTA" in txt:
        return ("limite de uso do plano grátis da IA atingido (muitas consultas em pouco tempo ou cota do dia). "
                "Espere 1 minuto e pergunte de novo")
    if cod in (401, 403) or "API_KEY" in txt or "PERMISSION_DENIED" in txt or "API KEY" in txt:
        return "a chave da IA foi recusada (confira GEMINI_API_KEY no Render)"
    if cod == 404 or "NOT_FOUND" in txt:
        return "o modelo configurado não foi encontrado (confira GEMINI_MODEL no Render)"
    if cod == 400 or "INVALID_ARGUMENT" in txt:
        return "a IA recusou o pedido (erro 400)"
    if cod in (500, 502, 503, 504) or "UNAVAILABLE" in txt:
        return ("os modelos do Google estão sobrecarregados agora (alta demanda, é temporário). "
                "Pergunte de novo em alguns minutos")
    if "TIMEOUT" in txt or "TIMED OUT" in txt:
        return "a IA demorou demais para responder"
    if "SEM RESPOSTA" in txt:
        return "a IA não devolveu texto (pode ter sido bloqueio de segurança)"
    if "CONNECT" in txt or "NAME RESOLUTION" in txt:
        return "não foi possível conectar ao serviço da IA"
    return f"erro inesperado ({type(e).__name__})"


# --------------------------------------------------------------------------- Gemini
def _historico_limpo(historico) -> list[tuple[str, str]]:
    """Últimas trocas da conversa (pergunta, resposta) — só texto, tamanho limitado."""
    saida = []
    for t in (historico or [])[-4:]:
        if isinstance(t, dict) and isinstance(t.get("pergunta"), str) and isinstance(t.get("resposta"), str):
            p, r = t["pergunta"].strip()[:1000], t["resposta"].strip()[:2500]
            if p and r:
                saida.append((p, r))
    return saida


# Falhas em que vale tentar OUTRO modelo: não existe para esta conta (404), cota do modelo (429), sobrecarga (5xx)
TROCA_DE_MODELO = (404, 429, 500, 502, 503, 504)
MAX_RESERVAS = 5
_reservas_descobertas: list[str] | None = None


def _versao(nome: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", nome)
    return float(m.group(1)) if m else 0.0


def _descobrir_reservas(cliente) -> list[str]:
    """Pergunta ao Google quais modelos 'flash' de texto esta chave pode usar (uma vez por processo)."""
    global _reservas_descobertas
    if _reservas_descobertas is not None:
        return _reservas_descobertas
    nomes = []
    try:
        for m in cliente.models.list():
            nome = (getattr(m, "name", "") or "").removeprefix("models/")
            acoes = getattr(m, "supported_actions", None) or []
            if acoes and "generateContent" not in acoes:
                continue
            if "flash" not in nome or any(x in nome for x in ("image", "tts", "audio", "live", "embed", "thinking")):
                continue
            nomes.append(nome)
    except Exception:
        return []            # não guarda: tenta descobrir de novo na próxima falha
    # mais novo primeiro; estável antes de preview. Os "lite" vêm intercalados: costumam ter cota grátis
    # própria e menos fila quando os "flash" completos estão sobrecarregados (503) ou no limite por minuto (429).
    chave = lambda n: (-_versao(n), "preview" in n or "exp" in n, n)  # noqa: E731
    cheios = sorted((n for n in nomes if "lite" not in n), key=chave)
    lites = sorted((n for n in nomes if "lite" in n), key=chave)
    ordem = []
    for i in range(max(len(cheios), len(lites))):
        ordem += cheios[i:i + 1] + lites[i:i + 1]
    _reservas_descobertas = ordem
    return ordem


def _modelos_reserva(cliente, principal: str) -> list[str]:
    fixos = [m.strip() for m in os.getenv("GEMINI_MODELOS_RESERVA", "").split(",") if m.strip()]
    vistos, saida = {principal}, []
    for m in fixos + _descobrir_reservas(cliente):
        if m not in vistos:
            vistos.add(m)
            saida.append(m)
    return saida[:MAX_RESERVAS]


def _conversa_gemini(cliente, modelo: str, conteudo: list, config, esperas) -> dict:
    from google.genai import types

    conteudo = list(conteudo)
    reg = _Registro()
    for _ in range(MAX_RODADAS):
        r = _com_nova_tentativa(lambda: cliente.models.generate_content(model=modelo, contents=conteudo, config=config),
                                esperas)
        chamadas = r.function_calls or []
        if not chamadas:
            if not (r.text or "").strip():
                # bloqueio de segurança, chamada malformada etc.: não fingir que "não há dados" — cair para o offline
                motivo = getattr(r, "prompt_feedback", None) or (r.candidates and r.candidates[0].finish_reason)
                raise RuntimeError(f"Gemini sem resposta: {motivo}")
            return {**reg.resultado(r.text), "modelo": modelo}
        conteudo.append(r.candidates[0].content)   # mantém a vez do modelo (inclui as assinaturas de raciocínio)
        respostas = []
        for ch in chamadas:
            saida = reg.executar(ch.name, ch.args)
            respostas.append(types.Part(function_response=types.FunctionResponse(
                id=ch.id, name=ch.name, response={"resultado": saida})))
        conteudo.append(types.Content(role="user", parts=respostas))
    return {**reg.resultado(""), "resposta": SEM_CONCLUSAO, "modelo": modelo}


def responder_gemini(pergunta: str, api_key: str, modelo: str, historico=None) -> dict:
    from google import genai  # import tardio: o sistema funciona sem o pacote no modo offline
    from google.genai import types

    cliente = genai.Client(api_key=api_key)
    declaracoes = []
    for nome, f in fer.FERRAMENTAS.items():
        d = {"name": nome, "description": f["descricao"]}
        if f["parametros"]:
            d["parameters_json_schema"] = {"type": "object", "properties": f["parametros"]}
        declaracoes.append(types.FunctionDeclaration(**d))
    config = types.GenerateContentConfig(
        system_instruction=_sistema(),
        tools=[types.Tool(function_declarations=declaracoes)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        # temperatura padrão: o Google recomenda não reduzir nos modelos Gemini 3 (pode causar repetição)
    )
    conteudo = []
    for p_ant, r_ant in _historico_limpo(historico):   # memória: o modelo entende "e na UBS X?"
        conteudo += [types.Content(role="user", parts=[types.Part(text=p_ant)]),
                     types.Content(role="model", parts=[types.Part(text=r_ant)])]
    conteudo.append(types.Content(role="user", parts=[types.Part(text=pergunta)]))

    # Modelo principal; se estiver sobrecarregado/sem cota/indisponível, tenta os modelos reserva.
    # Sem esperar nem repetir no mesmo modelo: no plano grátis cada modelo tem cota própria por minuto,
    # e repetir só gasta a cota (e o tempo do gestor). A espera só acontece no último modelo da fila.
    fila, reservas_carregadas, ultimo_erro = [modelo], False, None
    while fila:
        atual = fila.pop(0)
        esperas = (4,) if (reservas_carregadas and not fila) else ()
        try:
            return _conversa_gemini(cliente, atual, conteudo, config, esperas=esperas)
        except Exception as e:
            if _codigo(e) not in TROCA_DE_MODELO and "UNAVAILABLE" not in str(e).upper():
                raise
            ultimo_erro = e
            if not reservas_carregadas:
                reservas_carregadas = True
                fila += _modelos_reserva(cliente, modelo) or [modelo]   # sem reserva: o principal de novo, esperando
    raise ultimo_erro


# --------------------------------------------------------------------------- Claude
def responder_anthropic(pergunta: str, api_key: str, modelo: str, historico=None) -> dict:
    import anthropic  # import tardio: o sistema funciona sem o pacote no modo offline

    cliente = anthropic.Anthropic(api_key=api_key)
    sistema = _sistema()
    mensagens = []
    for p_ant, r_ant in _historico_limpo(historico):
        mensagens += [{"role": "user", "content": p_ant}, {"role": "assistant", "content": r_ant}]
    mensagens.append({"role": "user", "content": pergunta})
    reg = _Registro()

    for _ in range(MAX_RODADAS):
        r = cliente.messages.create(model=modelo, max_tokens=2000, system=sistema,
                                    tools=fer.definicoes_anthropic(), messages=mensagens)
        if r.stop_reason != "tool_use":
            return reg.resultado("".join(b.text for b in r.content if b.type == "text"))
        mensagens.append({"role": "assistant", "content": r.content})
        resultados = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            saida = reg.executar(b.name, b.input)
            resultados.append({"type": "tool_result", "tool_use_id": b.id,
                               "content": json.dumps(saida, ensure_ascii=False)})
        mensagens.append({"role": "user", "content": resultados})
    return {**reg.resultado(""), "resposta": SEM_CONCLUSAO}


def responder(pergunta: str, provedor: str, api_key: str, modelo: str, historico=None) -> dict:
    if provedor == "gemini":
        return responder_gemini(pergunta, api_key, modelo, historico)
    return responder_anthropic(pergunta, api_key, modelo, historico)
