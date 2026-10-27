(function () {
  const API = document.getElementById("script-analista").dataset.api;
  const msgs = document.getElementById("msgs");
  const vazio = document.getElementById("vazio");
  const form = document.getElementById("form-pergunta");
  const campo = document.getElementById("pergunta");
  const botao = document.getElementById("enviar");
  const memoria = document.getElementById("memoria");
  const limpar = document.getElementById("nova-conversa");
  const ubsSel = document.getElementById("ubs-analise");
  const perSel = document.getElementById("periodo-ia");
  const avatar = document.getElementById("tpl-avatar");
  const fmt = new Intl.NumberFormat("pt-BR");
  const fmt1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const NOMES = {
    cobertura_da_base: "cobertura da base", kpis_periodo: "KPIs do período", ranking_ubs: "ranking de UBS",
    faltas_por_dia_semana: "faltas por dia da semana", concentracao_por_horario: "horário agendado",
    evolucao_mensal: "evolução mensal", exames: "exames", producao_por_setor: "produção por setor",
    cruzamento_agendamento_producao: "cruzamento agendamento × produção",
    qualidade_nao_conformidades: "não conformidades", fila_de_triagem: "fila de triagem", alertas_gerenciais: "alertas",
    jornada_do_paciente: "jornada do paciente", faltas_repetidas: "faltas repetidas", serie_diaria: "dia a dia",
    comparar_periodos: "comparação de períodos", destaques_ubs: "UBS em destaque", detalhe_exame: "detalhe de exame",
    faltas_ubs_por_dia_semana: "UBS × dia da semana", exames_por_ubs: "exames por UBS", numeros_do_periodo: "números do período", presencas_ubs: "presenças por UBS",
  };
  const CONTINUAR = ["Compare com o mês anterior", "Detalhe por UBS", "O que fazer para melhorar?"];
  const ETAPAS = ["Interpretando a pergunta", "Consultando os indicadores no banco", "Cruzando os números", "Escrevendo a análise"];

  let historico = [];   // memória: últimas trocas (só texto), vão junto com a próxima pergunta
  let ocupado = false;

  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const rolar = () => { msgs.scrollTop = msgs.scrollHeight; };
  const botaoLink = (txt, fn) => { const b = el("button", "btn-link", txt); b.type = "button"; b.addEventListener("click", fn); return b; };

  function atualizarMemoria() {
    const n = historico.length;
    memoria.textContent = n ? `Memória: ${n} ${n === 1 ? "pergunta" : "perguntas"}` : "";
    memoria.title = n ? "A IA lembra as últimas perguntas desta conversa para entender continuações" : "";
    limpar.hidden = !n;
  }

  function autoAltura() { campo.style.height = "auto"; campo.style.height = Math.min(campo.scrollHeight, 180) + "px"; }

  function msgUsuario(t, periodo) {
    const m = el("div", "msg msg-usuario", t);
    if (periodo) m.append(el("span", "msg-periodo", periodo));
    msgs.append(m); rolar();
  }

  function baixarCSV(g) {
    const linhas = [["", ...g.series.map((s) => s.nome)]].concat(
      g.rotulos.map((r, i) => [r, ...g.series.map((s) => (s.valores[i] == null ? "" : String(s.valores[i]).replace(".", ",")))]));
    const csv = "﻿" + linhas.map((l) => l.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(";")).join("\r\n");
    const a = el("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    a.download = g.titulo.split(" · ")[0].replace(/[^\wÀ-ú -]/g, "").trim().replace(/\s+/g, "_") + ".csv";
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function grafico(box, g) {
    const series = g.series || [{ nome: g.titulo.split(" · ")[0], valores: g.valores || [] }];
    const fig = el("figure", "msg-grafico");
    const cab = el("figcaption");
    cab.append(el("span", null, g.titulo), botaoLink("Baixar dados (CSV)", () => baixarCSV({ ...g, series })));
    fig.append(cab);
    const horizontal = g.tipo === "barra_h";
    const area = el("div", "grafico"); area.style.height = (horizontal ? 40 + 26 * g.rotulos.length : 240) + "px";
    const cv = document.createElement("canvas");
    cv.setAttribute("role", "img"); cv.setAttribute("aria-label", g.titulo);
    area.append(cv); fig.append(area); box.append(fig);
    const { C } = G;
    const cores = [C.s1, C.s2, C.s3, C.s4];
    const linha = g.tipo === "linha";
    const rotulos = g.rotulos.map((r) => (/^\d{4}-\d{2}$/.test(r) ? G.mes(r) : r));
    const valFmt = (v) => (g.pct ? fmt1.format(v) + "%" : fmt.format(v));
    new Chart(cv, {
      type: linha ? "line" : "bar",
      data: { labels: rotulos, datasets: series.map((s, i) => ({ label: s.nome, pct: g.pct, data: s.valores,
        borderColor: cores[i % 4], backgroundColor: cores[i % 4], pointRadius: linha && rotulos.length < 20 ? 3 : 0 })) },
      options: {
        indexAxis: horizontal ? "y" : "x", layout: { padding: { right: horizontal ? 56 : 8 } },
        interaction: horizontal ? { mode: "nearest", axis: "y", intersect: false } : { mode: "index", intersect: false },
        plugins: { legend: { display: series.length > 1 } },
        scales: horizontal ? { x: g.pct ? G.eixoPct : G.eixoNum, y: G.semGrade }
                           : { x: { ...G.semGrade, ticks: { maxRotation: 0, autoSkipPadding: 10 } }, y: g.pct ? G.eixoPct : G.eixoNum },
      },
      plugins: horizontal ? [G.rotulos(valFmt)] : [],
    });
  }

  function acoes(box, r, pergunta) {
    const barra = el("div", "msg-acoes");
    const copiar = botaoLink("Copiar", async () => {
      try { await navigator.clipboard.writeText(r.resposta); copiar.textContent = "Copiado ✓"; }
      catch { copiar.textContent = "Não foi possível copiar"; }
      setTimeout(() => { copiar.textContent = "Copiar"; }, 1800);
    });
    const imprimir = botaoLink("Imprimir / salvar PDF", () => {
      box.classList.add("alvo-impressao"); document.body.classList.add("imprimindo");
      const titulo = el("div", "so-impressao");
      titulo.append(el("h2", null, "LACEN Intelligence · Analista IA"), el("p", null, pergunta));
      box.prepend(titulo);
      window.print();
      titulo.remove(); box.classList.remove("alvo-impressao"); document.body.classList.remove("imprimindo");
    });
    barra.append(copiar, imprimir);
    box.append(barra);
    const sug = el("div", "continuar");
    const proximas = (r.sugestoes && r.sugestoes.length) ? r.sugestoes : CONTINUAR;
    proximas.forEach((t) => { const b = el("button", "exemplo-q", t); b.type = "button"; b.addEventListener("click", () => perguntar(t)); sug.append(b); });
    box.append(sug);
  }

  function cabecalhoIA() {
    const autor = el("div", "ia-autor");
    if (avatar) autor.append(avatar.content.cloneNode(true));
    autor.append(el("b", null, "Analista"));
    return autor;
  }

  function msgIA(r, pergunta) {
    const box = el("div", "msg msg-ia");
    box.append(cabecalhoIA());
    if (r.aviso) box.append(el("div", "flash flash-aviso", r.aviso));
    const md = el("div", "md");
    // markdown → HTML sanitizado (a resposta nunca é inserida sem DOMPurify)
    md.innerHTML = DOMPurify.sanitize(marked.parse(r.resposta || ""), { USE_PROFILES: { html: true } });
    box.append(md);
    msgs.append(box);
    const graficos = r.graficos || (r.grafico ? [r.grafico] : []);
    if (window.Chart && window.G) graficos.forEach((g) => grafico(box, g));
    if (r.base) {
      const meta = el("div", "msg-meta");
      const item = (rot, val) => { const s = el("span"); s.append(rot + ": "); s.append(el("b", null, val)); return s; };
      meta.append(item("Base analisada", r.base.periodo), item("Registros", fmt.format(r.base.registros)),
                  item("Fontes", (r.ferramentas || []).map((f) => NOMES[f] || f).join(", ") || "—"),
                  item("Modo", r.modo === "LLM" ? "IA generativa" + (r.modelo ? ` (${r.modelo})` : "") : "analítico offline"));
      box.append(meta);
    }
    acoes(box, r, pergunta);
    rolar();
  }

  function carregando() {
    const box = el("div", "msg msg-ia digitando-etapas");
    box.append(cabecalhoIA());
    const lista = el("ol");
    ETAPAS.forEach((t) => lista.append(el("li", null, t)));
    box.append(lista);
    let i = 0;
    const passo = () => { [...lista.children].forEach((li, k) => { li.className = k < i ? "feito" : (k === i ? "atual" : ""); }); };
    passo();
    const t = setInterval(() => { if (i < ETAPAS.length - 1) { i++; passo(); } }, 1600);
    return { el: box, parar: () => clearInterval(t) };
  }

  async function perguntar(texto) {
    texto = (texto || "").trim();
    if (!texto || ocupado) return;
    ocupado = true;
    vazio.hidden = true;
    // período escolhido no seletor vale para as próximas perguntas (vai junto, de forma explícita)
    const periodo = perSel && perSel.value ? perSel.value : "";
    const envio = periodo && !texto.includes(periodo) ? `${texto}\n\n(Período da análise: ${periodo})` : texto;
    msgUsuario(texto, periodo);
    campo.value = ""; autoAltura(); botao.disabled = true;
    const c = carregando(); msgs.append(c.el); rolar();
    try {
      const resp = await fetch(API, { method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": LACEN.csrf() },
        body: JSON.stringify({ pergunta: envio, historico }) });
      const r = await resp.json();
      c.parar(); c.el.remove();
      if (!resp.ok) throw new Error(r.erro || "Erro ao consultar o analista.");
      msgIA(r, texto);
      historico.push({ pergunta: envio, resposta: (r.resposta || "").slice(0, 2500) });
      historico = historico.slice(-4);
      atualizarMemoria();
    } catch (e) {
      c.parar(); c.el.remove();
      msgs.append(el("div", "flash flash-erro", e.message || "Falha de comunicação com o servidor."));
    } finally { ocupado = false; botao.disabled = false; campo.focus(); }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); perguntar(campo.value); });
  campo.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); perguntar(campo.value); } });
  campo.addEventListener("input", autoAltura);
  document.querySelectorAll(".exemplo-q, .perguntar-alerta, #gerar-relatorio").forEach((b) =>
    b.addEventListener("click", () => perguntar(b.dataset.pergunta || b.textContent)));
  ubsSel.addEventListener("change", () => {
    if (!ubsSel.value) return;
    const per = perSel && perSel.value ? perSel.value : ubsSel.dataset.periodo;
    perguntar(`Faça um perfil completo da ${ubsSel.value} em ${per}: presenças, comparecimento e faltas, ` +
              "dia da semana com mais faltas, evolução mês a mês, exames e o que a gestão deveria fazer.");
    ubsSel.value = "";
  });
  limpar.addEventListener("click", () => {
    historico = []; atualizarMemoria();
    [...msgs.children].forEach((n) => { if (n !== vazio) n.remove(); });
    vazio.hidden = false;
  });
  atualizarMemoria();
})();
