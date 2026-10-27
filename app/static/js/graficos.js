// Padrões visuais dos gráficos (Chart.js) — tema escuro, tipografia do sistema, formatação pt-BR.
// Cores de série: paleta categórica validada para daltonismo sobre a superfície #131f29
// (ordem fixa; a cor segue a entidade, nunca a posição). Status só quando a cor significa bom/ruim.
(function () {
  const C = {
    // séries (ordem fixa)
    s1: "#3987e5", s2: "#d95926", s3: "#199e70", s4: "#c98500", s5: "#d55181", s6: "#008300", s7: "#9085e9", s8: "#e66767",
    // status
    bom: "#0ca30c", atencao: "#fab219", serio: "#ec835a", critico: "#d03b3b",
    // tinta e estrutura
    texto: "#aebdc8", mudo: "#7d909f", grade: "#1f2e3a", eixo: "#2c3f4f", superficie: "#131f29", neutro: "#4b5f70",
  };
  // apelidos usados pelas telas
  Object.assign(C, { azul: C.s1, laranja: C.s2, verde: C.s3, ambar: C.s4, roxo: C.s7, vermelho: C.critico,
                     azulClaro: "#2a4f7a", cinza: C.neutro });
  C.series = [C.s1, C.s2, C.s3, C.s4, C.s5, C.s6, C.s7, C.s8];

  const fmt = new Intl.NumberFormat("pt-BR");
  const fmt1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  Chart.defaults.font.family = '"Public Sans", system-ui, sans-serif';
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = C.texto;
  Chart.defaults.borderColor = C.grade;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.useBorderRadius = true;
  Chart.defaults.plugins.legend.labels.borderRadius = 2;
  Chart.defaults.plugins.legend.position = "bottom";
  Chart.defaults.plugins.legend.labels.color = C.texto;
  Chart.defaults.plugins.tooltip.backgroundColor = "#06101a";
  Chart.defaults.plugins.tooltip.borderColor = C.eixo;
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.titleColor = "#e8eef2";
  Chart.defaults.plugins.tooltip.bodyColor = "#c9d5de";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.boxPadding = 4;
  Chart.defaults.plugins.tooltip.footerFont = { weight: "normal" };
  Chart.defaults.plugins.tooltip.footerColor = "#9fb0bd";
  Chart.defaults.plugins.tooltip.footerMarginTop = 8;
  Chart.defaults.plugins.tooltip.callbacks.label = function (ctx) {
    // barras horizontais (indexAxis "y"): o valor está em x; em y fica só a posição da categoria
    const v = ctx.chart.options.indexAxis === "y" ? ctx.parsed.x : ctx.parsed.y;
    const pct = ctx.dataset.pct;
    const val = v == null ? "sem dado" : (pct ? fmt1.format(v) + "%" : fmt.format(v));
    return ` ${ctx.dataset.label}: ${val}`;
  };
  Chart.defaults.interaction = { mode: "index", intersect: false };
  Chart.defaults.elements.bar.borderRadius = 4;
  Chart.defaults.elements.bar.borderSkipped = "start";
  Chart.defaults.datasets.bar.maxBarThickness = 34;
  Chart.defaults.datasets.bar.categoryPercentage = 0.82;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.line.tension = 0.25;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 5;
  Chart.defaults.elements.point.hitRadius = 12;
  Chart.defaults.scale.grid.color = C.grade;
  Chart.defaults.scale.border = { color: C.eixo };
  Chart.defaults.scale.ticks.color = C.mudo;

  const meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];

  window.G = {
    C, fmt, fmt1,
    mes: (m) => `${meses[+m.slice(5, 7) - 1]}/${m.slice(2, 4)}`,
    dia: (d) => `${d.slice(8, 10)}/${d.slice(5, 7)}`,
    titulo: (s) => s.replace(/\b(UBS|CTA)\b/g, "#$1#").toLowerCase().replace(/(^|\s)\S/g, (t) => t.toUpperCase())
      .replace(/#(ubs|cta)#/gi, (_, x) => x.toUpperCase()).replace(/ (Da|Do|Dos|De|E) /g, (t) => t.toLowerCase()),
    eixoPct: { ticks: { callback: (v) => v + "%" }, beginAtZero: true },
    eixoNum: { ticks: { callback: (v) => fmt.format(v) }, beginAtZero: true },
    semGrade: { grid: { display: false } },
    criar(id, cfg) {
      const el = document.getElementById(id);
      return el ? new Chart(el, cfg) : null;
    },
    // botões segmentados (.seg): marca o escolhido e chama fn(modo)
    alternar(seletor, fn) {
      const grupo = document.querySelector(seletor); if (!grupo) return;
      grupo.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-modo]"); if (!b || b.classList.contains("ativo")) return;
        grupo.querySelectorAll("button").forEach((x) => { x.classList.toggle("ativo", x === b); x.setAttribute("aria-pressed", x === b); });
        fn(b.dataset.modo);
      });
    },
    // Comparecimento e faltas por mês: barras 100% empilhadas (as duas partes somam o total realizado)
    mensalComparecimento(id, mensal, metaFalta) {
      const m = mensal.filter((r) => r.taxa_comparecimento != null);
      const fx = (k, n) => (ctx) => { const r = m[ctx.dataIndex]; return ` ${ctx.dataset.label}: ${fmt1.format(r[k])}% (${fmt.format(r[n])})`; };
      return this.criar(id, {
        type: "bar",
        data: { labels: m.map((r) => this.mes(r.mes)), datasets: [
          { label: "Compareceram", data: m.map((r) => r.taxa_comparecimento), backgroundColor: C.s1, stack: "c",
            tooltip: { callbacks: { label: fx("taxa_comparecimento", "compareceram") } } },
          { label: "Faltaram", data: m.map((r) => r.taxa_falta), backgroundColor: C.s2, stack: "c",
            borderColor: C.superficie, borderWidth: { bottom: 2 }, borderSkipped: false, borderRadius: { topLeft: 4, topRight: 4 },
            tooltip: { callbacks: { label: fx("taxa_falta", "faltas") } } },
        ] },
        options: { layout: { padding: { right: 72 } }, scales: { x: { ...this.semGrade, stacked: true },
                             y: { stacked: true, min: 0, max: 100, ticks: { callback: (v) => v + "%", stepSize: 25 } } } },
        plugins: [this.linhaRef(100 - metaFalta, "Ref. " + (100 - metaFalta) + "%", "y")],
      });
    },
    // Barras horizontais com ordem (maior→menor, menor→maior, A→Z) e quantidade escolhidas em <select>s com
    // data-grafico="<id do canvas>" e data-controle="ordem|limite". chart.$visiveis = itens na ordem exibida.
    barrasControladas(chart, itens, { ordem = "desc", limite = 0, linha = 27, base = 70 } = {}) {
      if (!chart) return { atualizar() {} };
      const estado = { itens, ordem, limite };
      const caixa = chart.canvas.parentElement, col = new Intl.Collator("pt-BR");
      const ler = (sel) => (sel.dataset.controle === "limite" ? +sel.value : sel.value);
      const aplicar = () => {
        // "Top N" = os N maiores; depois a ordem escolhida (maior→menor, menor→maior ou A→Z)
        let v = estado.itens.filter((i) => i.valor != null).sort((a, b) => b.valor - a.valor);
        if (estado.limite) v = v.slice(0, estado.limite);
        if (estado.ordem === "az") v.sort((a, b) => col.compare(a.rotulo, b.rotulo));
        else if (estado.ordem === "asc") v.reverse();
        chart.$visiveis = v;
        chart.data.labels = v.map((i) => i.rotulo);
        const ds = chart.data.datasets[0];
        ds.data = v.map((i) => i.valor);
        ds.backgroundColor = v.map((i) => i.cor);
        caixa.style.height = (base + linha * Math.max(v.length, 3)) + "px";
        chart.resize(); chart.update();
      };
      document.querySelectorAll(`select[data-grafico="${chart.canvas.id}"]`).forEach((sel) => {
        estado[sel.dataset.controle] = ler(sel);
        sel.addEventListener("change", () => { estado[sel.dataset.controle] = ler(sel); aplicar(); });
      });
      aplicar();
      return { atualizar(novos) { estado.itens = novos; aplicar(); } };
    },
    // valor escrito na ponta de cada barra horizontal (ex.: "26,0%", "1.234")
    rotulos(formato = (v) => fmt.format(v)) {
      return {
        id: "rotulos",
        afterDatasetsDraw(chart) {
          const c = chart.ctx;
          c.save(); c.font = '600 11px "Public Sans", sans-serif'; c.fillStyle = "#e8eef2"; c.textBaseline = "middle";
          chart.data.datasets.forEach((ds, i) => {
            const meta = chart.getDatasetMeta(i); if (meta.hidden) return;
            meta.data.forEach((barra, j) => {
              const v = ds.data[j]; if (v == null) return;
              const txt = formato(v, j), w = c.measureText(txt).width;
              // fora da barra quando cabe; senão, dentro dela
              const x = barra.x + 6 + w <= chart.width - 4 ? barra.x + 6 : barra.x - w - 6;
              c.fillText(txt, x, barra.y);
            });
          });
          c.restore();
        },
      };
    },
    // linha de referência sólida e fina (ex.: média da rede, meta)
    // valor e rótulo podem ser funções (gráficos que alternam de indicador)
    linhaRef(valorOuFn, rotuloOuFn, eixo = "x", cor = "#aebdc8") {
      return {
        id: "ref-" + (typeof rotuloOuFn === "string" ? rotuloOuFn : "dinamica"),
        afterDatasetsDraw(chart) {
          const valor = typeof valorOuFn === "function" ? valorOuFn() : valorOuFn;
          const rotulo = typeof rotuloOuFn === "function" ? rotuloOuFn() : rotuloOuFn;
          const s = chart.scales[eixo]; if (!s || valor == null) return;
          const p = s.getPixelForValue(valor), a = chart.chartArea, c = chart.ctx;
          c.save(); c.strokeStyle = cor; c.lineWidth = 1.5; c.beginPath();
          if (eixo === "x") { c.moveTo(p, a.top); c.lineTo(p, a.bottom); } else { c.moveTo(a.left, p); c.lineTo(a.right, p); }
          c.stroke(); c.fillStyle = cor; c.font = '600 11px "Public Sans", sans-serif';
          // eixo x: rótulo acima da área do gráfico (o gráfico reserva espaço com layout.padding.top), longe dos valores das barras
          if (eixo === "x") { c.textAlign = "center"; c.fillText(rotulo, Math.max(a.left, Math.min(p, a.right - c.measureText(rotulo).width / 2)), a.top - 6); }
          // eixo y: rótulo FORA da área, à direita da linha (o gráfico reserva layout.padding.right ≈ 72)
          else { c.textBaseline = "middle"; c.fillText(rotulo, a.right + 6, p); }
          c.restore();
        },
      };
    },
  };
})();
