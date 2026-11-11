(() => {
  "use strict";

  const CONFIG = __DMT_CONFIG__;
  const previousModal = document.getElementById(CONFIG.modalId);
  if (previousModal) previousModal.remove();

  if (window.location.origin !== CONFIG.allowedOrigin) {
    window.alert(`Abre primero JIRA en ${CONFIG.dashboardUrl} y vuelve al módulo de la Toolbox.`);
    return;
  }

  const element = (tag, options = {}) => {
    const node = document.createElement(tag);
    if (options.id) node.id = options.id;
    if (options.text !== undefined) node.textContent = options.text;
    if (options.type) node.type = options.type;
    if (options.value !== undefined) node.value = options.value;
    if (options.style) node.style.cssText = options.style;
    if (options.title) node.title = options.title;
    return node;
  };

  const localDate = (value) =>
    `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;

  const parseLocalDate = (value) => new Date(`${value}T00:00:00`);

  const splitMonthlyRange = (startText, endText) => {
    const end = parseLocalDate(endText);
    const blocks = [];
    let cursor = parseLocalDate(startText);
    while (cursor <= end) {
      const monthEnd = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
      const blockEnd = monthEnd > end ? new Date(end) : monthEnd;
      blocks.push([localDate(cursor), localDate(blockEnd)]);
      cursor = new Date(blockEnd.getFullYear(), blockEnd.getMonth(), blockEnd.getDate() + 1);
    }
    return blocks;
  };

  const overlay = element("div", {
    id: CONFIG.modalId,
    style: "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:2147483647;display:flex;align-items:center;justify-content:center;padding:16px;font-family:Arial,sans-serif;color:#172b4d",
  });
  const panel = element("section", {
    style: "box-sizing:border-box;background:#fff;width:min(700px,100%);max-height:90vh;overflow:auto;border-radius:12px;padding:24px;box-shadow:0 15px 50px rgba(0,0,0,.4)",
  });
  const headingRow = element("div", {
    style: "display:flex;justify-content:space-between;align-items:flex-start;gap:16px",
  });
  const heading = element("div");
  heading.append(
    element("h2", { text: "Informe de Incurridos · BBVA", style: "margin:0;color:#072146;font-size:24px" }),
    element("p", { text: "Extracción consolidada de Worklogs desde Jira", style: "margin:6px 0 0;color:#5e6c84" }),
  );
  const closeButton = element("button", {
    text: "×",
    type: "button",
    title: "Cerrar",
    style: "border:0;background:transparent;font-size:28px;line-height:1;cursor:pointer;color:#42526e;padding:2px 6px",
  });
  closeButton.setAttribute("aria-label", "Cerrar informe de incurridos");
  closeButton.addEventListener("click", () => overlay.remove());
  headingRow.append(heading, closeButton);

  const divider = element("hr", { style: "border:0;border-top:1px solid #dfe1e6;margin:20px 0" });
  const dateRow = element("div", { style: "display:flex;flex-wrap:wrap;gap:20px;margin-bottom:20px" });
  const today = new Date();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);

  const makeDateField = (labelText, initialValue) => {
    const label = element("label", { text: labelText, style: "font-weight:600;display:grid;gap:6px" });
    const input = element("input", {
      type: "date",
      value: initialValue,
      style: "font:inherit;padding:8px;border:1px solid #a5adba;border-radius:5px;color:#172b4d",
    });
    label.append(input);
    return [label, input];
  };

  const [fromLabel, fromInput] = makeDateField("Desde", localDate(firstDay));
  const [toLabel, toInput] = makeDateField("Hasta", localDate(today));
  dateRow.append(fromLabel, toLabel);

  const projectTitle = element("strong", { text: "Proyectos" });
  const projectGrid = element("div", {
    style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:12px 0 20px",
  });
  const projectInputs = CONFIG.projects.map((project) => {
    const label = element("label", {
      style: "background:#f4f5f7;padding:9px;border-radius:5px;display:flex;align-items:center;gap:7px;cursor:pointer",
    });
    const input = element("input", { type: "checkbox", value: project });
    input.checked = true;
    label.append(input, document.createTextNode(project));
    projectGrid.append(label);
    return input;
  });

  const actions = element("div", { style: "display:flex;flex-wrap:wrap;gap:10px" });
  const generateButton = element("button", {
    text: "Generar informe",
    type: "button",
    style: "background:#1464a5;color:#fff;border:0;border-radius:5px;padding:11px 18px;font-weight:700;cursor:pointer",
  });
  const downloadButton = element("button", {
    text: "Descargar CSV",
    type: "button",
    style: "display:none;background:#197a4a;color:#fff;border:0;border-radius:5px;padding:11px 18px;font-weight:700;cursor:pointer",
  });
  const templateButton = element("button", {
    text: "Aplantillado",
    type: "button",
    style: "display:none;background:#6750a4;color:#fff;border:0;border-radius:5px;padding:11px 18px;font-weight:700;cursor:pointer",
  });
  actions.append(generateButton, downloadButton, templateButton);

  const status = element("div", {
    text: "Selecciona el periodo y los proyectos para comenzar.",
    style: "margin-top:20px;line-height:1.5;color:#42526e",
  });
  status.setAttribute("aria-live", "polite");
  const progressTrack = element("div", {
    style: "height:12px;background:#dfe1e6;border-radius:10px;overflow:hidden;margin-top:10px",
  });
  const progressBar = element("div", {
    style: "height:100%;width:0;background:#1464a5;transition:width .15s ease",
  });
  progressBar.setAttribute("role", "progressbar");
  progressBar.setAttribute("aria-valuemin", "0");
  progressBar.setAttribute("aria-valuemax", "100");
  progressBar.setAttribute("aria-valuenow", "0");
  progressTrack.append(progressBar);
  const errors = element("div", {
    style: "margin-top:15px;color:#ae2a19;font-size:12px;line-height:1.5;white-space:pre-wrap",
  });
  errors.setAttribute("aria-live", "polite");

  panel.append(
    headingRow,
    divider,
    dateRow,
    projectTitle,
    projectGrid,
    actions,
    status,
    progressTrack,
    errors,
  );
  overlay.append(panel);
  document.body.append(overlay);

  let result = null;

  const setProgress = (completed, total) => {
    const percentage = total ? Math.round((completed / total) * 100) : 0;
    progressBar.style.width = `${percentage}%`;
    progressBar.setAttribute("aria-valuenow", String(percentage));
  };

  const showErrors = (items) => {
    errors.replaceChildren();
    if (!items.length) return;
    errors.append(element("strong", { text: "Consultas con error:" }), document.createTextNode("\n"));
    items.forEach((message, index) => {
      errors.append(document.createTextNode(message));
      if (index < items.length - 1) errors.append(document.createTextNode("\n"));
    });
  };

  const validate = () => {
    const from = fromInput.value;
    const to = toInput.value;
    const projects = projectInputs.filter((input) => input.checked).map((input) => input.value);
    if (!from || !to || !projects.length) {
      window.alert("Selecciona ambas fechas y al menos un proyecto.");
      return null;
    }
    if (parseLocalDate(from) > parseLocalDate(to)) {
      window.alert("La fecha inicial no puede ser posterior a la final.");
      return null;
    }
    if (projects.some((project) => !CONFIG.projects.includes(project))) {
      window.alert("La selección contiene un proyecto no permitido.");
      return null;
    }
    return { from, to, projects };
  };

  generateButton.addEventListener("click", async () => {
    const selection = validate();
    if (!selection) return;

    const blocks = splitMonthlyRange(selection.from, selection.to);
    const totalQueries = blocks.length * selection.projects.length;
    let completed = 0;
    let header = null;
    const records = [];
    const failures = [];
    result = null;
    generateButton.disabled = true;
    generateButton.style.cursor = "wait";
    downloadButton.style.display = "none";
    templateButton.style.display = "none";
    if (window.pywebview?.api?.clear_jira_extraction) {
      await window.pywebview.api.clear_jira_extraction();
    }
    showErrors([]);
    setProgress(0, totalQueries);

    for (const project of selection.projects) {
      for (const block of blocks) {
        status.textContent = `Consultando ${project} · ${block[0]} → ${block[1]} · ${completed}/${totalQueries} consultas · ${records.length.toLocaleString("es-ES")} registros · ${failures.length} errores`;
        try {
          const parameters = new URLSearchParams({
            pkey: project,
            startDate: block[0],
            endDate: block[1],
          });
          const response = await window.fetch(`${CONFIG.endpoint}?${parameters.toString()}`, {
            method: "POST",
            headers: CONFIG.headers,
            body: JSON.stringify({ users: [] }),
            credentials: "same-origin",
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = await response.json();
          if (!payload || !Array.isArray(payload.data)) throw new Error("Respuesta sin datos");
          if (!payload.data.length || !Array.isArray(payload.data[0]) || !payload.data[0].length) {
            throw new Error("Respuesta sin una cabecera válida");
          }
          const currentHeader = payload.data[0].map((value) => String(value ?? ""));
          if (header && JSON.stringify(currentHeader) !== JSON.stringify(header)) {
            throw new Error("Cabecera incompatible con las consultas anteriores");
          }
          if (!header) header = currentHeader;
          for (const row of payload.data.slice(1)) {
            if (!Array.isArray(row)) throw new Error("La respuesta contiene una fila no válida");
            const dateValue = String(row[CONFIG.dateColumnIndex] ?? "");
            const validPeriod = /^\d{4}-\d{2}/.test(dateValue);
            records.push([
              project,
              validPeriod ? dateValue.slice(0, 4) : "",
              validPeriod ? dateValue.slice(5, 7) : "",
              validPeriod ? dateValue.slice(0, 7) : "",
              ...row,
            ]);
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          failures.push(`${project} · ${block[0]} → ${block[1]} · ${message}`);
        }
        completed += 1;
        setProgress(completed, totalQueries);
        await new Promise((resolve) => window.setTimeout(resolve, CONFIG.delayMs));
      }
    }

    generateButton.disabled = false;
    generateButton.style.cursor = "pointer";
    status.textContent = `Extracción completada · Periodo: ${selection.from} → ${selection.to} · Proyectos: ${selection.projects.length} · Consultas: ${completed} · Registros: ${records.length.toLocaleString("es-ES")} · Errores: ${failures.length}`;
    showErrors(failures);
    if (!header) {
      errors.append(document.createTextNode(`${failures.length ? "\n" : ""}No se ha obtenido una cabecera válida. La descarga no está disponible.`));
      return;
    }
    result = { from: selection.from, to: selection.to, pkeys: selection.projects, header, records };
    try {
      if (!window.pywebview?.api?.receive_jira_extraction) {
        throw new Error("El enlace con Delivery Manager Toolbox no está disponible");
      }
      const response = await window.pywebview.api.receive_jira_extraction({
        type: "jira_extraction_completed",
        payload: result,
      });
      if (!response?.ok) throw new Error(response?.error || "No se pudo transferir la extracción");
      if (response.newPeople?.length) {
        status.textContent += ` · Nuevas personas: ${response.newPeople.join(", ")}`;
      }
      if (response.warning) {
        errors.append(document.createTextNode(`${failures.length ? "\n" : ""}${response.warning}`));
      }
      downloadButton.style.display = "inline-block";
      templateButton.style.display = "inline-block";
    } catch (error) {
      result = null;
      errors.append(document.createTextNode(`${failures.length ? "\n" : ""}${error instanceof Error ? error.message : String(error)}`));
    }
  });

  downloadButton.addEventListener("click", async () => {
    if (!result) return;
    try {
      const response = await window.pywebview.api.get_jira_csv();
      if (!response?.ok) throw new Error(response?.error || "No se pudo generar el CSV");
      const binary = window.atob(response.contents);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const blob = new Blob([bytes], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = element("a");
      anchor.href = url;
      anchor.download = response.filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      errors.append(document.createTextNode(`\n${error instanceof Error ? error.message : String(error)}`));
    }
  });

  templateButton.addEventListener("click", async () => {
    if (!result) return;
    const response = await window.pywebview.api.open_aplantillado();
    if (!response?.ok) {
      errors.append(document.createTextNode(`\n${response?.error || "No se pudo abrir Aplantillado"}`));
    }
  });
})();
