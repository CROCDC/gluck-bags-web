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
  const undoBtn = root.querySelector("[data-ed-undo]");
  const findDraftsBtn = root.querySelector("[data-ed-find-drafts]");
  const SAVE_URL = root.dataset.saveUrl;

  // key -> the value the user has typed but not saved yet.
  const pending = Object.create(null);
  let manifest = { fields: {}, inlineKeys: [], hiddenKeys: [] };
  // key -> the field's own render(), so a change made anywhere (canvas, undo)
  // refreshes its counter. It used to only run on the textarea's own `input`.
  const renderers = Object.create(null);
  // Keys with a draft on the server that this page never rendered (staged on another
  // page, or in an earlier session). They still count and they still publish.
  let serverDrafts = [];
  // What the last Publicar put live, so it can be taken back from the same bar.
  let undoKeys = [];
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
    // Focus moves out before hiding: taking away the element the keyboard is standing
    // on drops focus to <body>, the same way a disabled Publicar used to.
    if (undoBtn && !undoBtn.hidden) {
      if (document.activeElement === undoBtn) {
        statusEl.setAttribute("tabindex", "-1");
        statusEl.focus();
      }
      undoBtn.hidden = true;
    }
    if (findDraftsBtn && !serverDrafts.length) findDraftsBtn.hidden = true;
    // An error now names the field AND the screen it lives on, which wraps the toolbar
    // onto a second line. The panel is positioned off the toolbar's height.
    syncBarHeight();
  }

  function dirtyCount() {
    return Object.keys(pending).length;
  }

  /** Everything that would go live on Publicar: what is typed here plus what is
   *  already drafted on the server. A union, because the same key can be in both —
   *  adding the two counts instead counted one pending text as two. */
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
    if (discardBtn) {
      // Its space is RESERVED (see .ed-btn.is-off), because a button appearing here
      // re-wrapped the whole toolbar: Guardar and Publicar jumped ~630px sideways and
      // 59px down, out from under the cursor that was going to click them. `hidden`
      // stays in the markup for the no-JS case and is dropped here.
      discardBtn.hidden = false;
      discardBtn.classList.toggle("is-off", total === 0);
    }
    // Showing Descartar adds a whole row to the fixed mobile bar, and the page reserves
    // exactly its height at the bottom.
    syncBarHeight();
  }

  /** A draft saved in an earlier session renders on the canvas exactly like the live
   *  copy, so without this the only clue that customers are not seeing it yet was a
   *  number on the Publicar button. */
  function draftNotice() {
    if (!serverDrafts.length) return "";
    if (findDraftsBtn) findDraftsBtn.hidden = false;
    return serverDrafts.length === 1
      ? "Tenés 1 texto guardado sin publicar: lo ves acá, pero tus clientes todavía no."
      : "Tenés " + serverDrafts.length +
        " textos guardados sin publicar: los ves acá, pero tus clientes todavía no.";
  }

  if (findDraftsBtn) {
    // Knowing a draft exists without knowing WHICH one left only two ways to find out,
    // and both of them were buttons you would not press just to look around.
    findDraftsBtn.addEventListener("click", () => {
      setPanel(true, false);
      if (filter) filter.value = "";
      // selectTab ends in applyFilter(), which un-hides everything the tab allows —
      // so the draft-only pass below has to come after it, not before.
      selectTab("all");
      root.querySelectorAll("[data-ed-fields] .ed-field").forEach((item) => {
        if (!hasDraft(item.dataset.edField)) item.hidden = true;
      });
      if (noResults) noResults.hidden = true;
      const first = root.querySelector("[data-ed-fields] .ed-field:not([hidden])");
      if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }

  window.addEventListener("beforeunload", (event) => {
    if (dirtyCount() === 0) return;
    event.preventDefault();
    event.returnValue = "";
  });

  /* ---------------- device frame ---------------- */

  let barSyncQueued = false;

  /** Publish the toolbar's real height so the panel can sit below it, and the height of
   *  the mobile action bar so the page can reserve exactly that much. Hard-coding either
   *  is what put the panel's close button under the toolbar, and the field being edited
   *  under the action bar.
   *
   *  Coalesced to one measurement per frame: the callers sit on the per-keystroke path
   *  and each read below forces a synchronous reflow. */
  function syncBarHeight() {
    if (barSyncQueued) return;
    barSyncQueued = true;
    window.requestAnimationFrame(() => {
      barSyncQueued = false;
      const bar = root.querySelector(".ed-bar");
      if (bar) root.style.setProperty("--ed-bar-h", Math.round(bar.offsetHeight) + "px");
      const actions = root.querySelector(".ed-actions");
      if (!actions) return;
      // Only below the breakpoint do the actions detach into a fixed bottom bar, and
      // its height changes with how many buttons are showing.
      const floating = window.getComputedStyle(actions).position === "fixed";
      root.style.setProperty(
        "--ed-actions-h",
        floating ? Math.round(actions.offsetHeight) + "px" : "0px"
      );
    });
  }

  /** The tallest the canvas may be without pushing the admin page into scrolling
   *  (which would slide the sticky toolbar over the panel's header). */
  /** The tallest the canvas may be without pushing the admin page into scrolling
   *  (which slides the sticky toolbar over the panel's header — its close button
   *  included; the panel cannot dodge it, because it is as tall as its own containing
   *  block and `sticky` has nowhere to move it to).
   *  Measured, not the old constant 210: that was the chrome of a ONE-ROW toolbar, and
   *  the bar grows a second row as soon as there is something to publish. */
  function viewportHeight() {
    // Where the toolbar does NOT float over the content (a phone: static bar, panel
    // under the canvas) the page is meant to scroll, and a taller canvas is the point.
    const bar = root.querySelector(".ed-bar");
    if (!bar || window.getComputedStyle(bar).position !== "sticky") {
      return Math.max(420, window.innerHeight - 210);
    }
    // Only what sits ABOVE the canvas is measured, and only that: it is the part that
    // grew (the toolbar now holds a second row open). Deriving the space BELOW from the
    // page height is self-referential — it moves with the canvas, so each call shrank it
    // a little further and the canvas collapsed to its 420px floor.
    const above = stage.getBoundingClientRect().top + window.scrollY;
    const footnote = root.querySelector(".ed-footnote");
    const below = footnote ? Math.ceil(footnote.getBoundingClientRect().height) + 24 : 56;
    return Math.max(420, window.innerHeight - above - below);
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

  // "Todos", not "No visibles": applyFilter() filters by TAB before it filters by query,
  // so opening on the narrow tab made the search answer "no results" for a text that is
  // right there on the page. The badge on the "No visibles" tab still sells it.
  let currentTab = "all";

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

  /** Saved, but the shop still shows the old wording. */
  function hasDraft(key) {
    return serverDrafts.indexOf(key) !== -1;
  }

  function valueOf(key) {
    return key in pending ? pending[key] : (fieldFor(key) || {}).raw || "";
  }

  function buildField(key) {
    const field = fieldFor(key);
    const wrap = document.createElement("div");
    wrap.className = "ed-field";
    wrap.dataset.edField = key;
    wrap.dataset.edSearch = searchable(field.label + " " + key + " " + valueOf(key));

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
    // Undoing one bad edit used to mean Descartar, which throws away every other change
    // too. It targets what the SHOP currently shows — not what the editor loaded, which
    // IS the bad draft once it has been saved, and not the factory text, which is a
    // different place entirely.
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "ed-field-restore ed-field-undo";
    undo.textContent = "Volver a lo que se ve en la web";
    undo.title = "El texto que tus clientes están viendo ahora";
    undo.addEventListener("click", () => {
      const live = field.live != null ? field.live : field.raw;
      input.value = live;
      stageChange(key, live);
      setStatus(
        hasDraft(key)
          ? "Listo. Tocá «Guardar borrador» o «Publicar cambios» para confirmarlo."
          : "Listo, volvió a lo que se ve en la web."
      );
    });
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "ed-field-restore";
    restore.textContent = "Volver al texto original";
    restore.title = "El texto con el que vino la web";
    foot.appendChild(count);
    foot.appendChild(undo);
    // Both links only DRAFT now, so looking alike stopped being a trap: what tells
    // them apart is where they land, which is why the destination is in the title.
    // Still gated on `previous`: with none, the wording this replaced WAS the factory
    // text and the link beside it already goes there.
    if (field.previous) {
      const revert = document.createElement("button");
      revert.type = "button";
      revert.className = "ed-field-restore";
      revert.textContent = "Volver a lo que decía antes";
      // Tag-free and trimmed: a `rich` field's previous value is a page of HTML.
      revert.title = field.previous
        .replace(/<[^>]*>/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 120);
      revert.addEventListener("click", () => revertKey([key]));
      foot.appendChild(revert);
    }
    foot.appendChild(restore);
    wrap.appendChild(foot);

    function render() {
      count.textContent = input.value.length + " / " + field.max;
      count.classList.toggle("is-over", input.value.length > field.max);
      const dirty = key in pending;
      wrap.classList.toggle("is-dirty", dirty);
      // The older form screen has flagged this since day one; with 91 fields in the
      // panel, "which ones did I leave saved?" was a memory game.
      wrap.classList.toggle("is-draft", !dirty && hasDraft(key));
      // Not `!dirty`: a draft that was already saved is not "dirty", and that was the
      // one state with no way back to the live wording at all.
      const live = field.live != null ? field.live : field.raw;
      undo.hidden = input.value === live;
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
    renderers[key] = render;
    return wrap;
  }

  function renderFields() {
    const hidden = new Set(manifest.hiddenKeys || []);
    hiddenBadge.textContent = String(hidden.size);

    const box = root.querySelector("[data-ed-fields]");
    box.textContent = "";
    Object.keys(renderers).forEach((key) => delete renderers[key]);
    const keys = Object.keys(manifest.fields);
    // Pending edits typed on ANOTHER page belong here too: they count towards
    // Publicar, and if one of them is invalid it blocks every save with nothing to
    // click. `pending` as well as `extraFields`, because an edit that was never
    // saved has no server-side draft to be listed from.
    Object.keys(extraFields)
      .concat(Object.keys(pending))
      .forEach((key) => {
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

  /** Bring back the wording that was live before the last publish — as a DRAFT.
   *
   *  It used to publish on the spot: one underlined link in the panel was the only
   *  control in the whole editor that changed the public site without going through
   *  "Publicar cambios", and it sat beside an identical-looking link that did not. A
   *  second click swapped straight back, so a double click published and unpublished
   *  the live site. The server decides WHAT the previous wording is, because the
   *  manifest this page loaded goes stale the moment a colleague publishes.
   */
  async function revertKey(keys) {
    const response = await fetch(root.dataset.revertUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys: keys }),
    }).catch(() => null);
    const payload = response ? await response.json().catch(() => null) : null;
    if (!payload || !payload.ok) {
      setStatus(
        payload && payload.errors && payload.errors.length
          ? payload.errors.join(" ")
          : "No pudimos recuperar el texto anterior.",
        true
      );
      return false;
    }
    // Before staging: a key drafted from another page only becomes renderable once
    // its metadata is here, and stageChange looks it up.
    serverDrafts = payload.pendingKeys || serverDrafts;
    extraFields = payload.pendingFields || extraFields;
    const values = payload.values || {};
    const names = Object.keys(values);
    // stageChange, so the canvas, the panel input, the dirty badge and the counter
    // all end up saying the same thing.
    names.forEach((key) => stageChange(key, values[key]));
    if (names.some((key) => !root.querySelector('[data-ed-field="' + CSS.escape(key) + '"]'))) {
      renderFields();
    }
    setStatus("Listo. Tocá «Publicar cambios» para que se vea en la web.");
    syncButtons();
    return true;
  }

  if (undoBtn) {
    undoBtn.addEventListener("click", async () => {
      const keys = undoKeys.slice();
      // The confirm IS the deliberate act that lets this reach the public site in one
      // step: the rule is that nothing goes live unattended, not that undoing has to
      // take two visits. Naming it "Deshacer" and then leaving a draft was a promise
      // the button did not keep.
      if (!window.confirm("¿Volvemos a como estaba antes de publicar?\n\nSe ve en la web enseguida.")) {
        return;
      }
      if (!(await revertKey(keys))) return;
      await send("publish", { undone: true });
      if (!dirtyCount()) setStatus("Listo, la web volvió a como estaba.");
    });
  }

  const filter = root.querySelector("[data-ed-filter]");
  const noResults = root.querySelector("[data-ed-no-results]");

  /** Lower-cased and stripped of accents. "titulo" used to find nothing at all, which
   *  reads as "that text does not exist" rather than "you missed a tilde". */
  function searchable(text) {
    return String(text)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function applyFilter() {
    const query = searchable((filter.value || "").trim());
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
    const original = (fieldFor(key) || {}).raw;
    if (value === original) delete pending[key];
    else pending[key] = value;
    syncButtons();
    setStatus(dirtyCount() ? "Cambios sin guardar" : draftNotice());
    // Keep the page and the panel showing the same thing.
    tellFrame({ type: "set", key: key, value: value });
    const box = root.querySelector('[data-ed-field="' + CSS.escape(key) + '"] input, [data-ed-field="' + CSS.escape(key) + '"] textarea');
    if (box && box.value !== value) box.value = value;
    root.querySelectorAll('[data-ed-field="' + CSS.escape(key) + '"]').forEach((item) => {
      item.classList.toggle("is-dirty", key in pending);
      const field = fieldFor(key) || {};
      item.dataset.edSearch = searchable((field.label || key) + " " + key + " " + value);
    });
    if (renderers[key]) renderers[key]();
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

  function reloadCanvas() {
    try {
      iframe.contentWindow.location.reload();
    } catch (_) {
      iframe.src = iframe.src;
    }
  }

  let flushToken = 0;
  const flushWaiters = Object.create(null);

  /** Commit whatever is being typed on the canvas, and only then act.
   *
   *  A toolbar click blurs the node being edited, and the frame defers that teardown a
   *  tick so the browser can settle focus first. Every button that reads `pending` was
   *  therefore reading it one edit behind — and worse, the late `change` message then
   *  overwrote whatever the button had just put in the status line, so a refused publish
   *  looked like a button that did nothing at all.
   */
  function flushFrame() {
    if (!iframe.contentWindow) return Promise.resolve();
    const token = ++flushToken;
    return new Promise((resolve) => {
      // Never hang on it: the canvas may be mid-navigation, and a toolbar that stops
      // responding is worse than acting on a value one keystroke old.
      const timer = window.setTimeout(() => {
        delete flushWaiters[token];
        resolve();
      }, 300);
      flushWaiters[token] = () => {
        window.clearTimeout(timer);
        delete flushWaiters[token];
        resolve();
      };
      tellFrame({ type: "flush", token: token });
    });
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
    // The SENDER has to be our own canvas. Origin alone let any same-origin script
    // (a nested frame, an extension, a future widget) drive openRich and land its
    // markup in `innerHTML` inside the admin document, next to the session cookie.
    if (!iframe.contentWindow || event.source !== iframe.contentWindow) return;
    const data = event.data || {};
    if (data.source !== "ct-frame" || typeof data.type !== "string") return;
    if (data.key !== undefined && typeof data.key !== "string") return;

    if (data.type === "flushed") {
      const done = flushWaiters[data.token];
      if (done) done();
    } else if (data.type === "ready") {
      manifest = data.manifest || manifest;
      // Follow the canvas: a link clicked inside the site used to strand the picker
      // on the old page, and re-selecting the same option fires no change event.
      if (pagePicker && data.path) {
        const match = Array.from(pagePicker.options).find((o) => o.value === data.path);
        pagePicker.value = match ? data.path : "";
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
  // The server sanitizes on save and on render; the editor document needs its own
  // pass, because what lands here comes over postMessage and goes into innerHTML.
  const RICH_TAGS = ["P", "BR", "STRONG", "B", "EM", "I", "H2", "H3", "UL", "OL", "LI", "A"];
  // Dropped WITH their contents, exactly like the server does: unwrapping a <style>
  // keeps its CSS as visible copy, so a paste from a word processor poured a stylesheet
  // into the page as text.
  const DROP_WITH_CONTENT = [
    "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "SVG", "MATH", "NOSCRIPT",
  ];

  function sanitizeRich(html) {
    const doc = document.implementation.createHTMLDocument("");
    doc.body.innerHTML = String(html == null ? "" : html);
    const walk = (node) => {
      // `children` skips comment nodes, so Word's <!--[if gte mso 9]><xml>…<![endif]-->
      // used to survive into the document — invisible, counted against the field's cap,
      // and dropped by the server on save. The server has handle_comment() for this.
      Array.from(node.childNodes).forEach((child) => {
        if (child.nodeType === Node.COMMENT_NODE) child.remove();
      });
      Array.from(node.children).forEach((child) => {
        // SVG and MathML elements report a lower-case tagName; normalize before matching.
        const tag = child.tagName.toUpperCase();
        if (DROP_WITH_CONTENT.indexOf(tag) !== -1) {
          child.remove();
          return;
        }
        if (RICH_TAGS.indexOf(tag) === -1) {
          child.replaceWith(...child.childNodes);
          return;
        }
        Array.from(child.attributes).forEach((attr) => {
          const keep = tag === "A" && ["href", "target", "rel"].indexOf(attr.name) !== -1;
          if (!keep) child.removeAttribute(attr.name);
        });
        if (tag === "A" && !safeHref(child.getAttribute("href"))) {
          child.replaceWith(...child.childNodes);
          return;
        }
        walk(child);
      });
    };
    walk(doc.body);
    return doc.body.innerHTML;
  }

  function safeHref(href) {
    const value = String(href || "").replace(/[\s\u0000-\u001f]/g, "");
    if (!value || /^(\/\/|\/\\|\\)/.test(value)) return false;
    if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
      return /^(https?|mailto|tel):/i.test(value);
    }
    return true;
  }

  function visibleLength(html) {
    const probe = document.createElement("div");
    probe.innerHTML = html;
    return (probe.textContent || "").replace(/\s+/g, " ").trim().length;
  }

  /** What the braces in this value stand for, in words.
   *  The sheet shows the RAW text ("el uso del sitio de {brand}"), so without this the
   *  only reasonable reading is that the shop's name is missing — and the fix anyone
   *  would try is to type it in, which kills the placeholder for good. */
  function tokenNote(value) {
    const known = manifest.tokens || {};
    const names = [];
    String(value == null ? "" : value).replace(/\{([a-z_][a-z0-9_]*)\}/g, (whole, name) => {
      if (names.indexOf(name) === -1 && Object.prototype.hasOwnProperty.call(known, name)) {
        names.push(name);
      }
      return whole;
    });
    if (!names.length) return "";
    return (
      "Dejá tal cual los textos entre llaves: se completan solos al publicar. " +
      names.map((name) => "{" + name + "} = " + known[name]).join(" · ")
    );
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
    // textContent, never innerHTML: a token's value comes from site_texts, so it is
    // user-controlled content being printed inside the admin origin.
    const tokens = root.querySelector("[data-ed-sheet-tokens]");
    tokens.textContent = tokenNote(value != null ? value : field.raw);
    tokens.hidden = !tokens.textContent;
    sheetDoc.innerHTML = sanitizeRich(value != null ? value : field.raw);
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
    showSheetNote("");
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
    // One quantity, shown and enforced: the counter used to display the visible
    // length and turn red on the HTML length, so it went red while reading "758".
    const visible = visibleLength(sheetDoc.innerHTML);
    const stored = sheetDoc.innerHTML.length;
    // Two raw numbers in different units, and the one that decides whether the save goes
    // through was the unexplained one. Only say something about the cap when the cap is
    // close enough to matter — a percentage nobody can act on is more noise than help.
    const room = field.max - stored;
    sheetCount.textContent =
      stored > field.max
        ? "Te pasaste del máximo: sacá texto o formato para poder guardar."
        : room < field.max * 0.15
        ? visible + " caracteres escritos · te queda poco lugar en esta página"
        : visible + " caracteres escritos";
    sheetCount.title =
      stored + " de " + field.max + " caracteres guardados (el formato también ocupa lugar).";
    sheetCount.classList.toggle("is-over", stored > field.max);
  }

  let sheetNoteTimer = null;

  /** A line under the toolbar saying what the editor just did to a paste. */
  function showSheetNote(text) {
    const note = root.querySelector("[data-ed-sheet-note]");
    if (!note) return;
    note.textContent = text || "";
    note.hidden = !text;
    window.clearTimeout(sheetNoteTimer);
    if (text) sheetNoteTimer = window.setTimeout(() => { note.hidden = true; }, 9000);
  }

  if (sheet) {
    sheetDoc.addEventListener("input", renderSheetCount);
    // What you see has to be what you keep: the server strips the colours, fonts and
    // <o:p> a word processor pastes in, so showing them until the next save is a promise
    // the save quietly breaks. Same allow-list the server uses, reused.
    sheetDoc.addEventListener("paste", (event) => {
      const data = event.clipboardData;
      if (!data) return;
      const html = data.getData("text/html");
      if (!html) return; // plain text: the browser inserts it clean already
      event.preventDefault();
      document.execCommand("insertHTML", false, sanitizeRich(html));
      renderSheetCount();
      if (/\sstyle=|\sclass=|<font\b|mso-/i.test(html)) {
        showSheetNote(
          "Pegamos el texto sin los colores ni las tipografías que traía. " +
            "Se mantienen la negrita, la cursiva, los subtítulos, las listas y los links."
        );
      }
    });
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
          if (href && !safeHref(href)) {
            // The server drops these silently on save, so the editor showed a link
            // that simply vanished later with no explanation.
            window.alert("Ese link no se puede usar. Tiene que empezar con https://");
          } else if (href) {
            document.execCommand("createLink", false, href);
          }
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

  /** `options.undone` marks the publish that a Deshacer is finishing: it must not arm
   *  another Deshacer for itself, and it says so in its own words. */
  async function send(action, options) {
    const undone = Boolean(options && options.undone);
    const changes = Object.assign({}, pending);
    // Captured once: it is what goes live, and what a Deshacer would have to take back.
    const scope = pendingKeys();
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
        body: JSON.stringify({ changes: changes, action: action, keys: scope }),
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
    const leftover = dirtyCount();
    setStatus(
      leftover
        ? "Guardado. Te quedaron " + leftover + " cambios más sin guardar."
        : action === "publish"
        ? "Publicado. Ya se ve en la web."
        : "Borrador guardado. Publicá cuando quieras."
    );
    // The way back belongs here, at the moment it is needed, instead of behind a panel
    // called "Textos ocultos", two tabs and a search away. Only on a clean publish:
    // with leftovers the status is about something else, and on a phone .ed-actions is
    // a fixed bar whose reserved height fits exactly one row.
    if (undoBtn && action === "publish" && scope.length && !leftover && !undone) {
      undoKeys = scope;
      undoBtn.hidden = false;
    }
    syncButtons();
    // `.src` reflects the ATTRIBUTE, which in-frame navigation never updates, so
    // re-assigning it threw the editor back to the page it started on.
    reloadCanvas();
  }

  saveBtn.addEventListener("click", async () => {
    await flushFrame();
    send("save");
  });

  /** The two rules the panel already draws on screen — empty, and past the cap. The
   *  server is still the real gate; this only stops us from asking "¿publicamos?" about
   *  something that was already condemned. */
  function localErrors(keys) {
    return keys.filter((key) => {
      const field = fieldFor(key);
      if (!field) return false;
      const value = String(valueOf(key));
      // Rich values are markup: what counts as empty is the text a reader would see, and
      // the server measures the SANITIZED string, which is never shorter.
      const written = field.type === "rich" ? visibleLength(value) : value.trim().length;
      return written === 0 || value.length > field.max;
    });
  }

  publishBtn.addEventListener("click", async () => {
    await flushFrame();
    const keys = pendingKeys();
    const bad = localErrors(keys);
    if (bad.length) {
      const names = bad.map((key) => "«" + ((fieldFor(key) || {}).label || key) + "»").join(", ");
      setStatus(
        "Todavía no se puede publicar. Revisá " + names +
          ": un texto no puede quedar vacío ni pasarse del largo máximo.",
        true
      );
      markInvalid(bad);
      return;
    }
    // Name what is about to go live. "¿Publicar los cambios?" gave no way to notice
    // that something parked days ago was riding along.
    const names = keys.map((key) => "· " + ((fieldFor(key) || {}).label || key));
    const shown = names.slice(0, 8).join("\n");
    const rest = names.length > 8 ? "\n… y " + (names.length - 8) + " más" : "";
    const one = keys.length === 1;
    const message =
      (one ? "Se va a publicar 1 texto:" : "Se van a publicar " + keys.length + " textos:") +
      "\n\n" + shown + rest +
      "\n\n" + (one ? "Se ve en la web enseguida." : "Se ven en la web enseguida.");
    if (window.confirm(message)) send("publish");
  });

  if (discardBtn) {
    discardBtn.addEventListener("click", async () => {
      const keys = pendingKeys();
      // The most destructive button in the editor was the only one that didn't say what
      // it was about to take with it.
      const names = keys.map((key) => "· " + ((fieldFor(key) || {}).label || key));
      const shown = names.slice(0, 8).join("\n");
      const rest = names.length > 8 ? "\n… y " + (names.length - 8) + " más" : "";
      const one = keys.length === 1;
      const message =
        (one
          ? "Vas a descartar este cambio sin publicar:"
          : "Vas a descartar los " + keys.length + " cambios sin publicar:") +
        "\n\n" + shown + rest +
        "\n\n" + (one ? "No se puede recuperar." : "No se pueden recuperar.");
      if (!window.confirm(message)) return;
      Object.keys(pending).forEach((key) => delete pending[key]);
      await fetch(root.dataset.discardUrl, { method: "POST" }).catch(() => null);
      serverDrafts = [];
      extraFields = {};
      setStatus("Cambios descartados.");
      syncButtons();
      reloadCanvas();
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
  setStatus(draftNotice());
})();
