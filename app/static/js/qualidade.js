(function () {
  const d = LACEN.dados();
  const { C } = G;
  // etapas do processo em ordem fixa de série (a cor segue a etapa)
  const etapas = [["pre", "Pré-analítica", C.s1], ["analitica", "Analítica", C.s2],
                  ["pos", "Pós-analítica", C.s3], ["adm", "Administrativa", C.s4]];
  G.criar("g-nc-mensal", {
    type: "bar",
    data: { labels: d.mensal.map((r) => G.mes(r.mes)),
      datasets: etapas.map(([k, rot, cor]) => ({ label: rot, data: d.mensal.map((r) => r[k]), backgroundColor: cor, stack: "nc",
        borderColor: C.superficie, borderWidth: { top: 2 }, borderSkipped: "bottom" })) },
    options: { scales: { x: { ...G.semGrade, stacked: true }, y: { ...G.eixoNum, stacked: true, ticks: { precision: 0 } } } },
  });
  // Pareto sem segundo eixo: barras ordenadas; o % acumulado aparece no rótulo de cada categoria.
  const pa = d.pareto || [];
  G.criar("g-pareto", {
    type: "bar",
    data: { labels: pa.map((r) => [r.categoria, G.fmt1.format(r.pct_acumulado) + "% acumulado"]),
      datasets: [{ label: "Não conformidades", data: pa.map((r) => r.qtd),
        backgroundColor: pa.map((r, i) => (i === 0 || (pa[i - 1] && pa[i - 1].pct_acumulado < 80) ? C.s1 : C.neutro)) }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false }, layout: { padding: { right: 40 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => it[0].label.split(",")[0] } } },
      scales: { x: { ...G.eixoNum, ticks: { precision: 0 } }, y: { ...G.semGrade, ticks: { font: { size: 11 } } } } },
    plugins: [G.rotulos()],
  });
})();
