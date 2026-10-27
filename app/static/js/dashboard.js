(function () {
  const d = LACEN.dados();
  const { C } = G;

  // Diário: uma escala só (contagens). Linha = total agendado no dia; barras = como esse total se dividiu.
  G.criar("g-diario", {
    type: "bar",
    data: {
      labels: d.diario.map((r) => G.dia(r.data)),
      datasets: [
        { type: "line", label: "Agendamentos", data: d.diario.map((r) => r.agendados),
          borderColor: C.s3, backgroundColor: C.s3, pointRadius: 2, order: 0 },
        { label: "Compareceram ou pendentes", data: d.diario.map((r) => r.agendados - r.faltas), backgroundColor: C.s1, stack: "a", order: 1 },
        { label: "Faltas", data: d.diario.map((r) => r.faltas), backgroundColor: C.s2, stack: "a", order: 1 },
      ],
    },
    options: { plugins: { tooltip: { callbacks: { footer: (it) => {
        const r = d.diario[it[0].dataIndex];
        return r.taxa_falta == null ? "" : [`Comparecimento: ${G.fmt1.format(r.taxa_comparecimento)}%`,
                                            `Taxa de faltas: ${G.fmt1.format(r.taxa_falta)}%`];
      } } } },
      scales: { x: { ...G.semGrade, stacked: true, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
                y: { ...G.eixoNum, stacked: true } } },
  });

  // Ranking: faltas, comparecimento (%) ou presenças (nº de pessoas que vieram) — botões acima do gráfico.
  // Faltas e comparecimento têm a mesma ordem (a UBS com mais faltas é a com menos comparecimento);
  // presenças mostra volume: a UBS que mais trouxe gente, independente do tamanho da agenda.
  const rk = d.ranking, media = d.taxa_rede;
  const pp = (v) => G.fmt1.format(v) + "%";
  const mediaPresencas = rk.length ? Math.round(rk.reduce((s, r) => s + (r.compareceram || 0), 0) / rk.length) : null;
  const MODOS = {
    falta: { titulo: "Taxa de faltas por UBS", rotulo: "Taxa de faltas", campo: "taxa_falta", rede: media, pct: true,
             refRotulo: () => "Rede " + pp(media || 0),
             conta: (r) => `${G.fmt.format(r.faltas)} faltas em ${G.fmt.format(r.realizados)} agendamentos realizados`,
             anterior: (r) => (r.taxa_falta_anterior == null ? null : `Período anterior: ${pp(r.taxa_falta_anterior)}`) },
    comparecimento: { titulo: "Taxa de comparecimento por UBS", rotulo: "Comparecimento", campo: "taxa_comparecimento", pct: true,
             rede: media == null ? null : Math.round((100 - media) * 10) / 10,
             refRotulo: () => "Rede " + pp(media == null ? 0 : 100 - media),
             conta: (r) => `${G.fmt.format(r.compareceram)} compareceram de ${G.fmt.format(r.realizados)} agendamentos realizados`,
             anterior: (r) => (r.taxa_falta_anterior == null ? null : `Período anterior: ${pp(100 - r.taxa_falta_anterior)}`) },
    presencas: { titulo: "UBS com mais presenças", rotulo: "Presenças", campo: "compareceram", pct: false,
             rede: mediaPresencas, refRotulo: () => "Média por UBS " + G.fmt.format(mediaPresencas || 0),
             conta: (r) => `${G.fmt.format(r.compareceram)} pessoas vieram · ${pp(r.taxa_comparecimento)} dos ${G.fmt.format(r.realizados)} agendamentos realizados`,
             anterior: (r) => (r.realizados_anterior ? `Período anterior: ${G.fmt.format(r.realizados_anterior - r.faltas_anterior)} presenças` : null) },
  };
  let modo = MODOS.falta;
  const itensRank = () => rk.map((r) => ({ rotulo: G.titulo(r.ubs), valor: r[modo.campo], dados: r,
                                           cor: modo.pct && r.taxa_falta >= media * 1.5 ? C.critico : C.s1 }));
  const gRank = G.criar("g-ranking", {
    type: "bar",
    data: { labels: [], datasets: [{ label: modo.rotulo, pct: true, data: [] }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false }, layout: { padding: { right: 52, top: 18 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { footer: (it) => {
        const r = gRank.$visiveis[it[0].dataIndex].dados, linhas = [modo.conta(r)], ant = modo.anterior(r);
        if (ant) linhas.push(ant);
        return linhas;
      } } } }, scales: { x: { ...G.eixoPct, max: undefined }, y: G.semGrade } },
    plugins: [G.linhaRef(() => modo.rede, () => modo.refRotulo()),
              G.rotulos((v) => (modo.pct ? pp(v) : G.fmt.format(v)))],
  });
  const ctrlRank = G.barrasControladas(gRank, itensRank(), { limite: 12 });
  const subRank = document.getElementById("s-ranking");
  G.alternar('.seg[data-alvo="g-ranking"]', (m) => {
    modo = MODOS[m];
    gRank.data.datasets[0].label = modo.rotulo;
    gRank.data.datasets[0].pct = modo.pct;
    gRank.options.scales.x = modo.pct ? { ...G.eixoPct, max: m === "comparecimento" ? 100 : undefined } : { ...G.eixoNum };
    document.getElementById("t-ranking").textContent = modo.titulo;
    if (subRank) subRank.textContent = modo.pct
      ? "UBS com 30+ agendamentos realizados · em vermelho: faltas 1,5× a rede ou mais"
      : "Número de pessoas que compareceram no período · UBS com 30+ agendamentos realizados";
    ctrlRank.atualizar(itensRank());
  });

  // Mensal: duas medidas de escalas diferentes = dois gráficos (nunca dois eixos no mesmo gráfico).
  const meses = d.mensal.map((r) => G.mes(r.mes));
  G.mensalComparecimento("g-mensal-falta", d.mensal, d.meta_falta);
  G.criar("g-mensal-atend", {
    type: "bar",
    data: { labels: meses, datasets: [{ label: "Atendimentos", data: d.mensal.map((r) => r.atendimentos), backgroundColor: C.s1 }] },
    options: { plugins: { legend: { display: false } }, scales: { x: G.semGrade, y: G.eixoNum } },
  });

  const gEx = G.criar("g-exames", {
    type: "bar",
    data: { labels: [], datasets: [{ label: "Exames", data: [] }] },
    options: { indexAxis: "y", interaction: { mode: "nearest", axis: "y", intersect: false }, layout: { padding: { right: 52 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { footer: (it) => {
        const r = gEx.$visiveis[it[0].dataIndex].dados, linhas = [`${r.sigla} · ${G.fmt1.format(r.participacao)}% de todos os exames do período`];
        if (r.variacao_pct != null) linhas.push(`Período anterior: ${G.fmt.format(r.quantidade_anterior)} (${r.variacao_pct > 0 ? "+" : ""}${G.fmt1.format(r.variacao_pct)}%)`);
        return linhas;
      } } } }, scales: { x: G.eixoNum, y: G.semGrade } },
    plugins: [G.rotulos()],
  });
  G.barrasControladas(gEx, d.top_exames.map((r) => ({ rotulo: r.exame, valor: r.quantidade, cor: C.s1, dados: r })), { limite: 8 });

  // UBS em destaque: alterna entre as listas
  G.alternar('.seg[data-alvo="ubs-destaque"]', (m) => {
    document.querySelectorAll(".top-ubs[data-lista]").forEach((ol) => { ol.hidden = ol.dataset.lista !== m; });
  });
})();
