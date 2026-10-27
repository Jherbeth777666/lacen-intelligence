(function () {
  const $ = (s) => document.querySelector(s);
  const exames = document.querySelectorAll("#exames input[type=checkbox]");
  const contar = () => {
    const n = [...exames].filter((c) => c.checked).length;
    $("#qtd-exames").textContent = n ? `(${n} selecionado${n > 1 ? "s" : ""})` : "";
  };
  exames.forEach((c) => c.addEventListener("change", contar));
  contar();

  $("#filtro-exame").addEventListener("input", (e) => {
    const t = e.target.value.trim().toLowerCase();
    document.querySelectorAll("#exames label").forEach((l) => { l.hidden = t && !l.dataset.busca.includes(t); });
  });

  const so = (v) => v.replace(/\D/g, "");
  $("#cpf").addEventListener("input", (e) => {
    const d = so(e.target.value).slice(0, 11);
    e.target.value = d.replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})\.(\d{3})(\d)/, "$1.$2.$3").replace(/(\d{3})-?(\d{1,2})$/, "$1-$2");
  });

  const achado = $("#paciente-achado"), dados = $("#dados-paciente");
  async function buscar() {
    const doc = so($("#doc").value);
    achado.hidden = false;
    if (![11, 15].includes(doc.length)) { achado.textContent = "Informe 11 dígitos (CPF) ou 15 (CNS)."; return; }
    achado.textContent = "A pesquisar…";
    try {
      const r = await fetch(achado.closest("form").action.replace(/\/nova.*$/, "/api/paciente"), {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": LACEN.csrf() },
      body: JSON.stringify({ documento: doc }),
    });
    if (!r.ok) throw new Error(`Erro HTTP: ${r.status}`);
    const p = await r.json();
    if (p.encontrado) {
      $("#paciente_id").value = p.id;
      dados.hidden = true;
      achado.textContent = "";
      const b = document.createElement("b"); b.textContent = p.nome;   // textContent: sem risco de XSS
      achado.append("Paciente encontrado: ", b, ` · ${p.idade} anos · ${p.sexo === "F" ? "Feminino" : "Masculino"} `);
      const trocar = document.createElement("button");
      trocar.type = "button"; trocar.className = "btn btn-p"; trocar.textContent = "Outro paciente";
      trocar.onclick = () => { $("#paciente_id").value = ""; dados.hidden = false; achado.hidden = true; $("#doc").value = ""; };
      achado.append(trocar);
      if (p.ubs_id && !$("#ubs_id").value) $("#ubs_id").value = p.ubs_id;
    } else {
      $("#paciente_id").value = "";
      dados.hidden = false;
      achado.textContent = "Paciente não cadastrado — preencha os dados abaixo.";
      if (doc.length === 11) $("#cpf").value = doc; else $("#cns").value = doc;
      $("#cpf").dispatchEvent(new Event("input"));
    }
    } catch (err) {
      achado.textContent = "Erro de ligação ao pesquisar paciente. Tenta novamente.";
      console.error("Erro na busca de paciente:", err);
    }
  }
  $("#btn-buscar").addEventListener("click", buscar);
  $("#doc").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscar(); } });
  if ($("#paciente_id").value) dados.hidden = true;
})();
