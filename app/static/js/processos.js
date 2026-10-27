(function () {
  const viewers = {};
  async function abrir(id) {
    const el = document.getElementById("bpmn-" + id);
    if (!el || viewers[id]) { if (viewers[id]) viewers[id].get("canvas").zoom("fit-viewport", "auto"); return; }
    const viewer = new BpmnJS({ container: el });
    viewers[id] = viewer;
    try {
      const xml = await (await fetch(el.dataset.src)).text();
      await viewer.importXML(xml);
      viewer.get("canvas").zoom("fit-viewport", "auto");
    } catch (e) {
      el.textContent = "Não foi possível carregar o diagrama.";
      console.error(e);
    }
  }
  abrir("asis");
  document.addEventListener("aba-mudou", (e) => setTimeout(() => abrir(e.detail), 30));
})();
