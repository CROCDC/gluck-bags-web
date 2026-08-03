// GLÜCK admin — visual editor shell (/admin/content/).
//
// Owns the pending changes and the saving; the in-frame script (editor-frame.js)
// owns the clicking and typing. They talk over postMessage, same-origin only.

(function () {
  "use strict";

  const root = document.querySelector("[data-ed]");
  if (!root) return;

  const iframe = root.querySelector("[data-ed-iframe]");
  const stage = root.querySelector("[data-ed-stage]");
  const frame = root.querySelector("[data-ed-frame]");
  const panel = root.querySelector("[data-ed-panel]");
  const statusEl = root.querySelector("[data-ed-status]");
  const saveBtn = root.querySelector("[data-ed-save]");
  const publishBtn = root.querySelector("[data-ed-publish]");
  const pendingBadge = root.querySelector("[data-ed-pending]");
  const hiddenBadge = root.querySelector("[data-ed-hidden-count]");
  const discardBtn = root.querySelector("[data-ed-discard]");
  const pagePickerEl = root.querySelector("[data-ed-page]");
  const SAVE_URL = root.dataset.saveUrl;

  // key -> the value the user has typed but not saved yet.
  const pending = Object.create(null);
  let manifest = { fields: {}, inlineKeys: [], hiddenKeys: [] };
  let savedPending = parseInt(root.dataset.pending, 10) || 0;
  // Keys with a draft on the server that this page never rendered (staged on another
  // page, or in an earlier session). They still count and they still publish.
  let serverDrafts = [];
  let inFlight = false;
  // Metadata for pending keys the current page does not render, so the panel can
  // still show (and fix) them.
  let extraFields = {};
  (function seedPending() {
    const node = root.querySelector("[data-ed-pending-state]");
    if (!node) return;
    try {
      const state = JSON.parse(node.textContent || "{}");
      serverDrafts = state.pendingKeys || [];
      extraFields = state.pendingFields || {};
    } catch (_) {}
  })();

  /* ---------------- status ---------------- */

  function setStatus(text, isError) {
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-error", Boolean(isError));
  }

  function dirtyCount() {
    return Object.keys(pending).length;
  }

  /** Everything that would go live on Publicar: what is typed here plus what is
   *  already drafted on the server. A union, because the same key can be in both —
   *  the old `dirty + savedPending` counted one pending text as two. */
  function pendingKeys() {
    const keys = new Set(Object.keys(pending));
    serverDrafts.forEach((key) => keys.add(key));
    return Array.from(keys);
  }

  function syncButtons() {
    const dirty = dirtyCount();
    const total = pendingKeys().length;
    saveBtn.disabled = inFlight || dirty === 0;
    publishBtn.disabled = inFlight || total === 0;
    pendingBadge.textContent = String(total);
    pendingBadge.hidden = total === 0;
    if (discardBtn) discardBtn.hidden = total === 0;
  }

  window.addEventListener("beforeunload", (event) => {
    if (dirtyCount() === 0) return;
    event.preventDefault();
    event.returnValue = "";
  });

  /* ---------------- device frame ---------------- */

  /** The tallest the canvas may be without pushing the admin page into scrolling
   *  (which would slide the sticky toolbar over the panel's header). */
  /** Publish the toolbar's real height so the panel can sit below it. Hard-coding it
   *  is what put the panel's close button underneath the bar. */
  function syncBarHeight() {
    const bar = root.querySelector(".ed-bar");
    if (bar) root.style.setProperty("--ed-bar-h", Math.round(bar.offsetHeight) + "px");
  }

  function viewportHeight() {
    return Math.max(420, window.innerHeight - 210);
  }

  function fitDevice(button) {
    const mode = button.dataset.edDevice;
    root.querySelectorAll("[data-ed-device]").forEach((other) => {
      const on = other === button;
      other.classList.toggle("is-current", on);
      other.setAttribute("aria-pressed", on ? "true" : "false");
    });
    syncBarHeight();
    const available = viewportHeight();
    if (panel) panel.style.maxHeight = available + "px";

    if (mode === "fluid") {
      stage.classList.add("is-fluid");
      frame.style.width = "";
      frame.style.transform = "";
      frame.style.height = available + "px";
      stage.style.height = "";
      return;
    }
    stage.classList.remove("is-fluid");
    const width = parseInt(button.dataset.width, 10);
    const height = parseInt(button.dataset.height, 10);
    const scale = Math.min(1, (stage.clientWidth - 26) / width, available / height);
    frame.style.width = width + "px";
    frame.style.height = height + "px";
    frame.style.transform = "scale(" + scale + ")";
    // transform doesn't affect layout, so the stage needs the scaled height.
    stage.style.height = height * scale + 26 + "px";
  }

  root.querySelectorAll("[data-ed-device]").forEach((button) => {
    button.addEventListener("click", () => fitDevice(button));
  });
  window.addEventListener("resize", () => {
    const current = root.querySelector("[data-ed-device].is-current");
    if (current) fitDevice(current);
  });

  /* ---------------- page picker ---------------- */

  const pagePicker = root.querySelector("[data-ed-page]");
  if (pagePicker) {
    pagePicker.addEventListener("change", () => {
      // Pending edits live in the shell, and the frame re-applies them on load, so
      // switching pages never loses what was typed.
      iframe.src = pagePicker.value + "?edit=1";
    });
  }

  /* ---------------- panel ---------------- */

  const panelToggle = root.querySelector("[data-ed-panel-toggle]");
  const panelClose = root.querySelector("[data-ed-panel-close]");

  function setPanel(open, takeFocus) {
    panel.hidden = !open;
    panelToggle.setAttribute("aria-expanded", open ? "true" : "false");
    const current = root.querySelector("[data-ed-device].is-current");
    if (current) window.setTimeout(() => fitDevice(current), 0);
    if (!open) {
      panelToggle.focus();
      return;
    }
    // Below the stacking breakpoint the panel opens hundreds of pixels down the
    // page: the toggle looked dead because nothing on screen changed. And leaving
    // focus on the toggle meant tabbing past the whole site to reach the panel.
    window.setTimeout(() => {
      panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
      if (takeFocus === false) return;
      const first = panel.querySelector("[data-ed-tab], input, textarea, button");
      if (first) first.focus();
    }, 30);
  }

  panelToggle.addEventListener("click", () => setPanel(panel.hidden));
  panelClose.addEventListener("click", () => setPanel(false));

  let currentTab = "hidden";

  function selectTab(name) {
    currentTab = name;
    root.querySelectorAll("[data-ed-tab]").forEach((tab) => {
      const on = tab.dataset.edTab === name;
      tab.classList.toggle("is-current", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    const showCards = name === "cards";
    root.querySelector('[data-ed-tabpanel="fields"]').hidden = showCards;
    root.querySelector('[data-ed-tabpanel="cards"]').hidden = !showCards;
    root.querySelector("[data-ed-note]").hidden = name !== "hidden";
    if (showCards) fillCards();
    else applyFilter();
  }

  const tabButtons = Array.from(root.querySelectorAll("[data-ed-tab]"));
  tabButtons.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.edTab));
    // Roving tabindex + arrow keys: the role promised this and nothing implemented it,
    // so all three were separate tab stops and the arrows did nothing.
    tab.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = tabButtons[(index + step + tabButtons.length) % tabButtons.length];
      selectTab(next.dataset.edTab);
      next.focus();
    });
  });

  function fieldFor(key) {
    return manifest.fields[key] || extraFields[key];
  }

  function valueOf(key) {
    return key in pending ? pending[key] : (fieldFor(key) || {}).raw || "";
  }

  function buildField(key) {
    const field = fieldFor(key);
    const wrap = document.createElement("div");
    wrap.className = "ed-field";
    wrap.dataset.edField = key;
    wrap.dataset.edSearch = (field.label + " " + key + " " + valueOf(key)).toLowerCase();

    const label = document.createElement("label");
    label.className = "ed-field-label";
    label.textContent = field.label;
    label.htmlFor = "ed-" + key;
    wrap.appendChild(label);

    const where = document.createElement("span");
    where.className = "ed-field-where";
    where.textContent = field.groupTitle + (field.section ? " · " + field.section : "");
    wrap.appendChild(where);

    const multiline = field.type === "text" || field.type === "lines" || field.type === "rich";
    const input = document.createElement(multiline ? "textarea" : "input");
    input.id = "ed-" + key;
    if (!multiline) input.type = field.type === "url" ? "url" : "text";
    else input.rows = field.type === "rich" ? 6 : 3;
    input.value = valueOf(key);
    input.maxLength = field.max;
    wrap.appendChild(input);

    if (field.hint) {
      const hint = document.createElement("p");
      hint.className = "ed-field-hint";
      hint.textContent = field.hint;
      wrap.appendChild(hint);
    }

    const foot = document.createElement("div");
    foot.className = "ed-field-foot";
    const count = document.createElement("span");
    count.className = "ed-field-count";
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "ed-field-restore";
    restore.textContent = "Restaurar original";
    foot.appendChild(count);
    // A way back to yesterday's wording, not just to the factory text.
    if (field.previous) {
      const revert = document.createElement("button");
      revert.type = "button";
      revert.className = "ed-field-restore";
      revert.textContent = "Volver al texto anterior";
      revert.addEventListener("click", () => revertKey(key, input));
      foot.appendChild(revert);
    }
    foot.appendChild(restore);
    wrap.appendChild(foot);

    function render() {
      count.textContent = input.value.length + " / " + field.max;
      count.classList.toggle("is-over", input.value.length > field.max);
      const dirty = key in pending;
      wrap.classList.toggle("is-dirty", dirty);
      restore.hidden = input.value === field.default;
    }

    if (field.type === "rich" && /<(p|h2|h3|ul|ol|li)\b/i.test(String(field.raw || ""))) {
      // A whole editorial page in a 6-row textarea of raw HTML is how a stray </p>
      // breaks the page. Send it to the same editor the canvas uses.
      input.readOnly = true;
      input.title = "Abrir el editor de esta página";
      // Deliberately NOT on `focus`: closing the sheet returns focus here, which
      // would immediately reopen it. Click, or Enter/Space for the keyboard.
      input.addEventListener("click", () => openSheet(key, valueOf(key)));
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openSheet(key, valueOf(key));
      });
    }
    input.addEventListener("input", () => {
      stageChange(key, input.value);
      render();
    });
    restore.addEventListener("click", () => {
      input.value = field.default;
      stageChange(key, field.default);
      render();
    });

    render();
    return wrap;
  }

  function renderFields() {
    const hidden = new Set(manifest.hiddenKeys || []);
    hiddenBadge.textContent = String(hidden.size);

    const box = root.querySelector("[data-ed-fields]");
    box.textContent = "";
    const keys = Object.keys(manifest.fields);
    // Pending edits typed on ANOTHER page belong here too: they count towards
    // Publicar, and if one of them is invalid it blocks every save.
    Object.keys(extraFields).forEach((key) => {
      if (keys.indexOf(key) === -1) keys.push(key);
    });
    keys.forEach((key) => {
      if (!fieldFor(key)) return;
      const item = buildField(key);
      // Not visible on the page = only reachable from here, so the "No visibles"
      // tab is really a filter over this same list.
      const elsewhere = !manifest.fields[key];
      item.dataset.edHidden = hidden.has(key) || elsewhere ? "1" : "0";
      if (elsewhere) item.dataset.edElsewhere = "1";
      box.appendChild(item);
    });
    applyFilter();
  }

  async function revertKey(key, input) {
    const response = await fetch(root.dataset.revertUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key }),
    }).catch(() => null);
    const payload = response ? await response.json().catch(() => null) : null;
    if (!payload || !payload.ok) {
      setStatus("No pudimos volver al texto anterior.", true);
      return;
    }
    delete pending[key];
    if (input) input.value = payload.value;
    const field = fieldFor(key);
    if (field) field.raw = payload.value;
    tellFrame({ type: "set", key: key, value: payload.value });
    setStatus("Listo, volvimos al texto anterior.");
    syncButtons();
  }

  const filter = root.querySelector("[data-ed-filter]");
  const noResults = root.querySelector("[data-ed-no-results]");

  function applyFilter() {
    const query = (filter.value || "").trim().toLowerCase();
    let shown = 0;
    root.querySelectorAll("[data-ed-fields] .ed-field").forEach((item) => {
      const matchesTab = currentTab !== "hidden" || item.dataset.edHidden === "1";
      const matchesQuery = !query || item.dataset.edSearch.indexOf(query) !== -1;
      const show = matchesTab && matchesQuery;
      item.hidden = !show;
      if (show) shown++;
    });
    if (noResults) noResults.hidden = shown !== 0;
  }

  if (filter) filter.addEventListener("input", applyFilter);

  /* ---------------- change tracking ---------------- */

  function stageChange(key, value) {
    const original = (manifest.fields[key] || {}).raw;
    if (value === original) delete pending[key];
    else pending[key] = value;
    syncButtons();
    setStatus(dirtyCount() ? "Cambios sin guardar" : "");
    // Keep the page and the panel showing the same thing.
    tellFrame({ type: "set", key: key, value: value });
    const box = root.querySelector('[data-ed-field="' + CSS.escape(key) + '"] input, [data-ed-field="' + CSS.escape(key) + '"] textarea');
    if (box && box.value !== value) box.value = value;
    root.querySelectorAll('[data-ed-field="' + CSS.escape(key) + '"]').forEach((item) => {
      item.classList.toggle("is-dirty", key in pending);
      const field = manifest.fields[key] || {};
      item.dataset.edSearch = (field.label + " " + key + " " + value).toLowerCase();
    });
  }

  function markInvalid(keys) {
    root.querySelectorAll(".ed-field.is-invalid").forEach((item) => {
      item.classList.remove("is-invalid");
      const box = item.querySelector("input, textarea");
      if (box) {
        box.removeAttribute("aria-invalid");
        box.removeAttribute("aria-describedby");
      }
    });
    tellFrame({ type: "highlight", keys: keys });
    if (!keys.length) return;
    setPanel(true, false);
    filter.value = "";
    selectTab("all");
    keys.forEach((key) => {
      const item = root.querySelector('[data-ed-field="' + CSS.escape(key) + '"]');
      if (!item) return;
      item.classList.add("is-invalid");
      const box = item.querySelector("input, textarea");
      if (box) {
        box.setAttribute("aria-invalid", "true");
        box.setAttribute("aria-describedby", "edStatus");
      }
    });
    const first = root.querySelector(".ed-field.is-invalid");
    if (first) {
      window.setTimeout(() => {
        first.scrollIntoView({ block: "center" });
        const box = first.querySelector("input, textarea");
        if (box) box.focus();
      }, 60);
    }
  }

  function tellFrame(message) {
    if (!iframe.contentWindow) return;
    iframe.contentWindow.postMessage(
      Object.assign({ source: "ct-shell" }, message),
      window.location.origin
    );
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data || {};
    if (data.source !== "ct-frame") return;

    if (data.type === "ready") {
      manifest = data.manifest || manifest;
      // Follow the canvas: a link clicked inside the site used to strand the picker
      // on the old page, and re-selecting the same option fires no change event.
      if (pagePickerEl && data.path) {
        const match = Array.from(pagePickerEl.options).find((o) => o.value === data.path);
        pagePickerEl.value = match ? data.path : "";
      }
      // Re-apply anything edited before navigating to this page.
      Object.keys(pending).forEach((key) => {
        if (manifest.fields[key]) tellFrame({ type: "set", key: key, value: pending[key] });
      });
      renderFields();
      syncButtons();
      const current = root.querySelector("[data-ed-device].is-current");
      if (current) fitDevice(current);
    } else if (data.type === "change") {
      stageChange(data.key, data.value);
    } else if (data.type === "openRich") {
      openSheet(data.key, data.value);
    } else if (data.type === "openKeys") {
      // The caller focuses a specific field below; don't let the panel's own
      // deferred focus take it back.
      setPanel(true, false);
      const first = (data.keys || [])[0];
      if (!first) return;
      // Show everything, so a key that is ALSO visible on the page still resolves.
      filter.value = "";
      selectTab("all");
      const target = root.querySelector('[data-ed-field="' + CSS.escape(first) + '"]');
      if (target) {
        window.setTimeout(() => {
          target.scrollIntoView({ block: "center" });
          const box = target.querySelector("input, textarea");
          if (box) box.focus();
        }, 50);
      }
    }
  });

  /* ---------------- page-body editor ---------------- */

  const sheet = root.querySelector("[data-ed-sheet]");
  const sheetDoc = root.querySelector("[data-ed-sheet-doc]");
  const sheetCount = root.querySelector("[data-ed-sheet-count]");
  let sheetKey = null;
  let sheetReturnFocus = null;

  /** What a reader actually sees. The old counter measured the HTML, so an emptied
   *  body reported "7 / 12000" and a paste of markup burned budget invisibly. */
  function visibleLength(html) {
    const probe = document.createElement("div");
    probe.innerHTML = html;
    return (probe.textContent || "").replace(/\s+/g, " ").trim().length;
  }

  function openSheet(key, value) {
    const field = fieldFor(key);
    if (!field) return;
    sheetKey = key;
    sheetReturnFocus = document.activeElement;
    root.querySelector("[data-ed-sheet-title]").textContent = field.label;
    root.querySelector("[data-ed-sheet-where]").textContent =
      field.section && field.section !== field.groupTitle
        ? field.groupTitle + " · " + field.section
        : field.groupTitle;
    sheetDoc.innerHTML = value != null ? value : field.raw;
    sheet.hidden = false;
    document.body.classList.add("ed-sheet-open");
    // Belt and braces: `inert` removes the background from the tab order and from
    // the accessibility tree in browsers that support it.
    if ("inert" in HTMLElement.prototype) {
      Array.from(root.children).forEach((child) => {
        if (child !== sheet) child.inert = true;
      });
    }
    renderSheetCount();
    sheetDoc.focus();
  }

  function closeSheet() {
    sheet.hidden = true;
    document.body.classList.remove("ed-sheet-open");
    if ("inert" in HTMLElement.prototype) {
      Array.from(root.children).forEach((child) => {
        child.inert = false;
      });
    }
    sheetKey = null;
    if (sheetReturnFocus && sheetReturnFocus.focus) sheetReturnFocus.focus();
  }

  function renderSheetCount() {
    const field = sheetKey ? fieldFor(sheetKey) : null;
    if (!field) return;
    const visible = visibleLength(sheetDoc.innerHTML);
    sheetCount.textContent = visible + " caracteres";
    sheetCount.classList.toggle("is-over", sheetDoc.innerHTML.length > field.max);
  }

  if (sheet) {
    sheetDoc.addEventListener("input", renderSheetCount);
    root.querySelectorAll("[data-ed-sheet-cancel]").forEach((btn) =>
      btn.addEventListener("click", closeSheet)
    );
    root.querySelector("[data-ed-sheet-apply]").addEventListener("click", () => {
      const key = sheetKey;
      const html = sheetDoc.innerHTML;
      closeSheet();
      if (key) {
        stageChange(key, html);
        setStatus("Texto actualizado. Guardá o publicá cuando quieras.");
      }
    });
    root.querySelectorAll("[data-ed-cmd]").forEach((btn) => {
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.addEventListener("click", () => {
        sheetDoc.focus();
        const cmd = btn.dataset.edCmd;
        if (cmd === "createLink") {
          const href = window.prompt("¿A qué link lleva? (https://…)");
          if (href) document.execCommand("createLink", false, href);
        } else {
          document.execCommand(cmd, false, btn.dataset.edArg || null);
        }
        renderSheetCount();
      });
    });
    // A dialog that declares aria-modal="true" has to behave like one: focus stays
    // inside until it closes, or a keyboard user tabs straight out into a page that
    // is visually behind an overlay and cannot be seen.
    const FOCUSABLE =
      'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

    function sheetFocusables() {
      return Array.from(sheet.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetWidth || el.offsetHeight || el === document.activeElement
      );
    }

    sheet.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSheet();
        return;
      }
      if (event.key !== "Tab") return;
      const items = sheetFocusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    // Focus that escapes some other way (a click on the page behind, a programmatic
    // focus) gets pulled back in.
    document.addEventListener("focusin", (event) => {
      if (sheet.hidden || sheet.contains(event.target)) return;
      const items = sheetFocusables();
      if (items.length) items[0].focus();
    });
  }

  /* ---------------- saving ---------------- */

  async function send(action) {
    const changes = Object.assign({}, pending);
    // Capture this BEFORE syncButtons() disables the button under the user's fingers:
    // a disabled element drops focus to <body>, which is where a keyboard user lost
    // their place at the most important moment of the flow.
    const hadFocus = document.activeElement === saveBtn || document.activeElement === publishBtn;
    inFlight = true;
    syncButtons();
    if (hadFocus) {
      statusEl.setAttribute("tabindex", "-1");
      statusEl.focus();
    }
    setStatus(action === "publish" ? "Publicando…" : "Guardando…");

    let payload = null;
    let failure = null;
    // A hung request used to leave the buttons dead and "Guardando…" on screen
    // forever, with no way out.
    const abort = new AbortController();
    const timeout = window.setTimeout(() => abort.abort(), 20000);
    try {
      const response = await fetch(SAVE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: changes, action: action, keys: pendingKeys() }),
        signal: abort.signal,
      });
      payload = await response.json().catch(() => null);
      if (response.status === 401 || (payload && payload.reason === "auth")) {
        failure = "Se cerró tu sesión. Abrí el admin en otra pestaña, entrá de nuevo y volvé a guardar — no perdiste nada.";
      } else if (!response.ok || !payload || !payload.ok) {
        failure = payload && payload.errors && payload.errors.length
          ? payload.errors.join(" ")
          : "No pudimos guardar. Probá de nuevo en un momento.";
      }
    } catch (error) {
      failure =
        error.name === "AbortError"
          ? "El guardado tardó demasiado. Fijate tu conexión y probá de nuevo."
          : "No pudimos guardar: revisá tu conexión. Tus cambios siguen acá.";
    } finally {
      window.clearTimeout(timeout);
      inFlight = false;
    }

    if (failure) {
      setStatus(failure, true);
      markInvalid((payload && payload.errorKeys) || []);
      syncButtons();
      return;
    }

    markInvalid([]);
    // Only drop what was actually sent AND is still what we sent: anything typed
    // while the request was in flight was never included, and used to be deleted
    // under a success message.
    Object.keys(changes).forEach((key) => {
      if (pending[key] === changes[key]) delete pending[key];
    });
    serverDrafts = payload.pendingKeys || [];
    extraFields = payload.pendingFields || {};
    savedPending = payload.pending || 0;
    const leftover = dirtyCount();
    setStatus(
      leftover
        ? "Guardado. Te quedaron " + leftover + " cambios más sin guardar."
        : action === "publish"
        ? "Publicado. Ya se ve en la web."
        : "Borrador guardado. Publicá cuando quieras."
    );
    syncButtons();
    // Reload the canvas so it shows exactly what is stored; anything still pending is
    // re-applied by the frame's `ready` handshake.
    iframe.src = iframe.src;
  }

  saveBtn.addEventListener("click", () => send("save"));

  publishBtn.addEventListener("click", () => {
    const keys = pendingKeys();
    // Name what is about to go live. "¿Publicar los cambios?" gave no way to notice
    // that something parked days ago was riding along.
    const names = keys.map((key) => "· " + ((fieldFor(key) || {}).label || key));
    const shown = names.slice(0, 8).join("\n");
    const rest = names.length > 8 ? "\n… y " + (names.length - 8) + " más" : "";
    const message =
      "Se van a publicar " + keys.length + (keys.length === 1 ? " texto:" : " textos:") +
      "\n\n" + shown + rest + "\n\nSe ven en la web enseguida.";
    if (window.confirm(message)) send("publish");
  });

  if (discardBtn) {
    discardBtn.addEventListener("click", async () => {
      const keys = pendingKeys();
      if (!window.confirm("¿Descartar " + keys.length + " cambio(s) sin publicar? No se puede deshacer.")) return;
      Object.keys(pending).forEach((key) => delete pending[key]);
      await fetch(root.dataset.discardUrl, { method: "POST" }).catch(() => null);
      serverDrafts = [];
      extraFields = {};
      setStatus("Cambios descartados.");
      syncButtons();
      iframe.src = iframe.src;
    });
  }

  /* ---------------- share/search cards ---------------- */

  function fillCards() {
    let doc = null;
    try {
      doc = iframe.contentDocument;
    } catch (_) {
      return;
    }
    if (!doc || !doc.head) return;
    const pick = (sel, attr) => {
      const el = doc.head.querySelector(sel);
      return el ? (el.getAttribute(attr || "content") || "").trim() : "";
    };
    const canonical = pick('link[rel="canonical"]', "href") || pick('meta[property="og:url"]');
    let host = canonical;
    try {
      host = new URL(canonical).host;
    } catch (_) {}

    const set = (sel, text) => {
      const el = root.querySelector(sel);
      if (el) el.textContent = text || "";
    };
    const image = (sel, url) => {
      const el = root.querySelector(sel);
      if (!el) return;
      if (url) {
        el.src = url;
        el.hidden = false;
      } else {
        el.removeAttribute("src");
        el.hidden = true;
      }
    };

    set("[data-ct-serp-url]", canonical);
    set("[data-ct-serp-title]", (doc.title || "").trim());
    set("[data-ct-serp-desc]", pick('meta[name="description"]'));

    set("[data-ct-og-title]", pick('meta[property="og:title"]') || doc.title);
    set("[data-ct-og-desc]", pick('meta[property="og:description"]'));
    set("[data-ct-og-host]", host);
    set("[data-ct-og-url]", canonical);
    image("[data-ct-og-image]", pick('meta[property="og:image"]'));

    set("[data-ct-tw-host]", host);
    set("[data-ct-tw-title]", pick('meta[name="twitter:title"]') || doc.title);
    set("[data-ct-tw-desc]", pick('meta[name="twitter:description"]'));
    image("[data-ct-tw-image]", pick('meta[name="twitter:image"]'));
  }

  iframe.addEventListener("load", () => {
    const cardsTab = root.querySelector('[data-ed-tab="cards"]');
    if (cardsTab && cardsTab.classList.contains("is-current")) fillCards();
  });

  const skipLink = root.querySelector("[data-ed-skip]");
  if (skipLink) {
    skipLink.addEventListener("click", (event) => {
      event.preventDefault();
      setPanel(true);
    });
  }

  const initial = root.querySelector("[data-ed-device].is-current");
  if (initial) fitDevice(initial);
  syncButtons();
})();
