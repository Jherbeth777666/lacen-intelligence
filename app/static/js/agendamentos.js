(function () {
  const d = LACEN.dados();
  const { C } = G;
  const maxSemana = Math.max(...d.dia_semana.map((r) => r.taxa_falta || 0));
  G.criar("g-semana", {
    type: "bar",
    data: { labels: d.dia_semana.map((r) => r.dia_semana),
      datasets: [{ label: "Taxa de faltas", pct: true, data: d.dia_semana.map((r) => r.taxa_falta),
        backgroundColor: d.dia_semana.map((r) => (r.taxa_falta === maxSemana ? C.critico : C.s1)) }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { footer: (it) => {
        const r = d.dia_semana[it[0].dataIndex];
        return r.taxa_falta == null ? "" : [`Comparecimento: ${G.fmt1.format(100 - r.taxa_falta)}%`,
                                            `${G.fmt.format(r.faltas)} faltas em ${G.fmt.format(r.realizados)} realizados`];
      } } } }, scales: { x: G.semGrade, y: G.eixoPct } },
  });
  G.mensalComparecimento("g-mensal", d.mensal, d.meta_falta);
  G.criar("g-horario", {
    type: "bar",
    data: { labels: d.horario.map((r) => r.horario),
      datasets: [{ label: "Agendamentos", data: d.horario.map((r) => r.agendamentos), backgroundColor: C.s1 }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false }, layout: { padding: { right: 52 } },
      plugins: { legend: { display: false } }, scales: { x: G.eixoNum, y: G.semGrade } },
    plugins: [G.rotulos()],
  });

  // Matriz de prioridade: volume (x) × taxa de faltas (y). Linhas = média de volume e taxa da rede.
  const mz = d.matriz || [];
  if (mz.length) {
    const mediaX = mz.reduce((a, r) => a + r.realizados, 0) / mz.length, rede = d.taxa_rede;
    const prioridade = (r) => r.realizados > mediaX && rede != null && r.taxa_falta > rede;
    const nomes = {
      id: "nomes-prioridade",
      afterDatasetsDraw(chart) {
        // nomes só nos pontos prioritários (e na UBS selecionada); cada nome tenta direita, esquerda,
        // e um pouco acima/abaixo — se não couber sem encostar em outro, fica só na dica (hover)
        const c = chart.ctx, a = chart.chartArea, pts = chart.getDatasetMeta(0).data, usados = [];
        c.save(); c.font = '600 11px "Public Sans", sans-serif'; c.fillStyle = "#e8eef2"; c.textBaseline = "middle";
        const bate = (r) => usados.some((u) => r.x < u.x + u.w && u.x < r.x + r.w && r.y < u.y + u.h && u.y < r.y + r.h)
          || pts.some((pt) => pt.x > r.x - 6 && pt.x < r.x + r.w + 6 && pt.y > r.y - 4 && pt.y < r.y + r.h + 4);
        mz.map((r, i) => i).filter((i) => prioridade(mz[i]) || mz[i].ubs_id === d.ubs_id)
          .sort((i, j) => (mz[j].ubs_id === d.ubs_id) - (mz[i].ubs_id === d.ubs_id) || pts[i].y - pts[j].y)
          .forEach((i) => {
            const pt = pts[i], txt = mz[i].ubs, w = c.measureText(txt).width, h = 14;
            for (const dy of [0, -13, 13]) {
              for (const x of [pt.x + 11, pt.x - 11 - w]) {
                const r = { x, y: pt.y + dy - h / 2, w, h };
                if (x < a.left || x + w > a.right || bate(r)) continue;
                usados.push(r); c.fillText(txt, x, pt.y + dy); return;
              }
            }
          });
        c.restore();
      },
    };
    G.criar("g-matriz", {
      type: "scatter",
      data: { datasets: [{ label: "UBS", data: mz.map((r) => ({ x: r.realizados, y: r.taxa_falta })),
        backgroundColor: mz.map((r) => (prioridade(r) ? C.critico : C.s1)),
        borderColor: mz.map((r) => (r.ubs_id === d.ubs_id ? "#ffffff" : C.superficie)),
        borderWidth: mz.map((r) => (r.ubs_id === d.ubs_id ? 3 : 2)),
        pointRadius: 7, pointHoverRadius: 9, pointHitRadius: 10 }] },
      options: {
        interaction: { mode: "nearest", intersect: true },
        layout: { padding: { top: 18, right: 72 } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          title: (it) => mz[it[0].dataIndex].ubs,
          label: (ctx) => { const r = mz[ctx.dataIndex];
            return [` Taxa de faltas: ${G.fmt1.format(r.taxa_falta)}%`,
                    ` ${G.fmt.format(r.realizados)} agendamentos realizados · ${G.fmt.format(r.faltas)} faltas`]; },
          footer: (it) => (prioridade(mz[it[0].dataIndex]) ? "Acima da média em volume e em faltas" : ""),
        } } },
        scales: {
          x: { ...G.eixoNum, title: { display: true, text: "Agendamentos realizados", color: C.mudo } },
          y: { ...G.eixoPct, title: { display: true, text: "Taxa de faltas", color: C.mudo } },
        },
      },
      plugins: [G.linhaRef(mediaX, "Média " + G.fmt.format(Math.round(mediaX))),
                G.linhaRef(rede, "Rede " + G.fmt1.format(rede || 0) + "%", "y"), nomes],
    });
  }

  // Pacientes por número de faltas no período
  const rp = d.repetidas || [];
  G.criar("g-repetidas", {
    type: "bar",
    data: { labels: rp.map((r) => r.faixa),
      datasets: [{ label: "Pacientes", data: rp.map((r) => r.pacientes),
        backgroundColor: rp.map((r, i) => (i === 0 ? C.neutro : C.s2)) }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false }, layout: { padding: { right: 52 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        footer: (it) => `${G.fmt.format(rp[it[0].dataIndex].faltas)} faltas no total` } } },
      scales: { x: { ...G.eixoNum, ticks: { precision: 0 } }, y: G.semGrade } },
    plugins: [G.rotulos()],
  });
})();
