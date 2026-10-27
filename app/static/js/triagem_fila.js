(function () {
  const d = LACEN.dados();
  const { C } = G;
  G.criar("g-triagem-dia", {
    type: "bar",
    data: { labels: d.diario.map((r) => G.dia(r.data)),
      datasets: [
        { label: "Rotina", data: d.diario.map((r) => r.total - r.urgentes), backgroundColor: C.s1, stack: "t" },
        { label: "Prioritária ou urgente", data: d.diario.map((r) => r.urgentes), backgroundColor: C.s2, stack: "t" },
      ] },
    options: { scales: { x: { ...G.semGrade, stacked: true, ticks: { maxRotation: 0, autoSkipPadding: 14 } },
                         y: { ...G.eixoNum, stacked: true, ticks: { precision: 0 } } } },
  });
})();
