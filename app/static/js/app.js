// Comportamentos globais (sem JS inline, compatível com a CSP)
(function () {
  const lateral = document.getElementById("lateral");
  const abrir = document.getElementById("abrir-menu");
  if (abrir && lateral) {
    abrir.addEventListener("click", (e) => { e.stopPropagation(); lateral.classList.toggle("aberta"); });
    document.addEventListener("click", (e) => {
      if (lateral.classList.contains("aberta") && !lateral.contains(e.target)) lateral.classList.remove("aberta");
    });
  }
  // fecha mensagens
  document.querySelectorAll("[data-fechar]").forEach((b) => b.addEventListener("click", () => b.parentElement.remove()));
  // formulários de filtro que enviam ao mudar
  document.querySelectorAll("form[data-auto] select, form[data-auto] input[type=date]").forEach((el) =>
    el.addEventListener("change", () => el.form.submit()));
  // confirmação antes de ações irreversíveis
  document.querySelectorAll("form[data-confirmar]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirmar)) e.preventDefault(); }));
  // abas
  document.querySelectorAll("[data-abas]").forEach((grupo) => {
    const botoes = grupo.querySelectorAll("[data-aba]");
    botoes.forEach((b) => b.addEventListener("click", () => {
      botoes.forEach((x) => x.classList.toggle("ativa", x === b));
      document.querySelectorAll(grupo.dataset.abas).forEach((p) => { p.hidden = p.id !== b.dataset.aba; });
      document.dispatchEvent(new CustomEvent("aba-mudou", { detail: b.dataset.aba }));
    }));
  });
  // tabelas ordenáveis: <table data-ordenavel>; clique no título ordena (de novo inverte).
  // Valor da célula: data-v, senão o texto (números no formato brasileiro, %, p.p.). <th data-sem-ordem> fica fora.
  const col = new Intl.Collator("pt-BR", { numeric: true, sensitivity: "base" });
  const numero = (t) => {
    t = t.replace(/p\.p\./g, "").replace(/−/g, "-").replace(/[^\d,.\-+]/g, "");
    if (!/\d/.test(t)) return null;
    const n = parseFloat(t.replace(/\./g, "").replace(",", "."));
    return Number.isNaN(n) ? null : n;
  };
  document.querySelectorAll("table[data-ordenavel]").forEach((tab) => {
    const corpo = tab.tBodies[0];
    if (!corpo) return;
    const linhas = () => [...corpo.rows].filter((r) => r.cells.length > 1);
    [...tab.tHead.rows[0].cells].forEach((th, i) => {
      if (th.hasAttribute("data-sem-ordem")) return;
      th.classList.add("ordenavel"); th.tabIndex = 0;
      const ordenar = () => {
        const vals = linhas().map((r) => { const c = r.cells[i]; return c ? (c.dataset.v ?? c.textContent.trim()) : ""; });
        const nums = vals.map(numero), numerica = nums.filter((n) => n != null).length >= vals.filter((v) => v && v !== "—").length * 0.6;
        const atual = th.getAttribute("aria-sort");
        const asc = atual ? atual === "descending" : !numerica;          // 1º clique: números do maior; texto de A a Z
        [...tab.tHead.rows[0].cells].forEach((x) => x.removeAttribute("aria-sort"));
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        const chave = (r) => { const c = r.cells[i]; const v = c ? (c.dataset.v ?? c.textContent.trim()) : ""; return numerica ? numero(v) : v; };
        linhas().sort((a, b) => {
          const x = chave(a), y = chave(b);
          if (x == null || x === "" || x === "—") return 1;               // vazios sempre no fim
          if (y == null || y === "" || y === "—") return -1;
          const r = numerica ? x - y : col.compare(x, y);
          return asc ? r : -r;
        }).forEach((r) => corpo.append(r));
      };
      th.addEventListener("click", ordenar);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ordenar(); } });
    });
  });
})();

window.LACEN = {
  dados() {
    const el = document.getElementById("dados");
    return el ? JSON.parse(el.textContent) : {};
  },
  csrf() {
    const el = document.querySelector("input[name=_csrf]");
    return el ? el.value : "";
  },
};
