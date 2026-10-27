(function () {
  const d = LACEN.dados();
  const { C } = G;
  // Cor do setor segue o setor (ordem alfabética fixa), nunca a posição no gráfico.
  const todos = [...new Set(d.tat_mensal.map((r) => r.setor).concat(d.setores.map((s) => s.setor)))]
    .filter((s) => s !== "Não classificado").sort((a, b) => a.localeCompare(b, "pt-BR"));
  const corSetor = (s) => C.series[todos.indexOf(s) % C.series.length];
  const meses = [...new Set(d.tat_mensal.map((r) => r.mes))].sort();
  const setores = todos.filter((s) => d.tat_mensal.some((r) => r.setor === s));
  G.criar("g-tat", {
    type: "line",
    data: { labels: meses.map(G.mes),
      datasets: setores.map((s) => ({ label: s, borderColor: corSetor(s), backgroundColor: corSetor(s), pointRadius: 3,
        data: meses.map((m) => (d.tat_mensal.find((r) => r.mes === m && r.setor === s) || {}).tat_medio_h ?? null) })) },
    options: { scales: { x: G.semGrade, y: { ...G.eixoNum, ticks: { callback: (v) => v + " h" } } } },
  });
  G.criar("g-diario", {
    type: "bar",
    data: { labels: d.diario.map((r) => G.dia(r.data)),
      datasets: [{ label: "Exames", data: d.diario.map((r) => r.exames), backgroundColor: C.s1 }] },
    options: { plugins: { legend: { display: false } },
      scales: { x: { ...G.semGrade, ticks: { maxRotation: 0, autoSkipPadding: 12 } }, y: G.eixoNum } },
  });
  // Divergente: alta (azul) × queda (laranja), a partir do zero. Crescer não é "bom" nem "ruim" — por isso não usa verde/vermelho.
  const vr = d.variacao || [];
  G.criar("g-variacao", {
    type: "bar",
    data: { labels: vr.map((r) => r.exame),
      datasets: [{ label: "Variação", pct: true, data: vr.map((r) => r.variacao_pct),
        backgroundColor: vr.map((r) => (r.variacao_pct >= 0 ? C.s1 : C.s2)),
        borderSkipped: false }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        label: (ctx) => { const r = vr[ctx.dataIndex]; return ` ${(r.variacao_pct > 0 ? "+" : "") + G.fmt1.format(r.variacao_pct)}% (${G.fmt.format(r.quantidade_anterior)} → ${G.fmt.format(r.quantidade)})`; } } } },
      scales: { x: { ticks: { callback: (v) => (v > 0 ? "+" : "") + v + "%" } }, y: G.semGrade } },
  });
  const pf = d.perfil || [];
  G.criar("g-perfil", {
    type: "bar",
    data: { labels: pf.map((r) => r.rotulo),
      datasets: [{ label: "Atendimentos", data: pf.map((r) => r.atendimentos), backgroundColor: C.s3 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (it) => `${it[0].label} exame(s) no atendimento` } } },
      scales: { x: { ...G.semGrade, title: { display: true, text: "exames pedidos no atendimento", color: C.mudo } }, y: G.eixoNum } },
  });
})();
