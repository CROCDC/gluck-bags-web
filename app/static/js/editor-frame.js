// Runs INSIDE the public page when a logged-in admin opens it with ?edit=1.
// (Injected by app/content/editor_markup.py — never present on a normal request.)
//
// Its job: turn the <ct-t> wrappers the server emitted into click-to-edit text,
// and report every change up to the editor shell over postMessage. It owns no
// state: the shell holds the pending changes and does the saving.

(function () {
  "use strict";

  const manifestNode = document.getElementById("ctManifest");
  if (!manifestNode) return;

  let MANIFEST;
  try {
    MANIFEST = JSON.parse(manifestNode.textContent || "{}");
  } catch (_) {
    return;
  }

  const FIELDS = MANIFEST.fields || {};
  const TOKENS = MANIFEST.tokens || {};
  // Raw value per key, kept in sync as the user types so several occurrences of the
  // same string (a nav label in the header, the menu and the footer) stay identical.
  const CURRENT = {};
  Object.keys(FIELDS).forEach((key) => {
    CURRENT[key] = FIELDS[key].raw;
  });

  /* ---------------- helpers ---------------- */

  function post(message) {
    if (window.parent === window) return;
    window.parent.postMessage(Object.assign({ source: "ct-frame" }, message), window.origin);
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** `escaped` mirrors the server: inside a rich value a token is DATA, so it is
   *  escaped before splicing. Substituting raw and then assigning innerHTML made the
   *  canvas execute what the public page escapes. */
  function interpolate(raw, escaped) {
    return String(raw).replace(/\{([a-z_][a-z0-9_]*)\}/g, (whole, name) => {
      if (!Object.prototype.hasOwnProperty.call(TOKENS, name)) return whole;
      return escaped ? escapeHtml(TOKENS[name]) : TOKENS[name];
    });
  }

  function parseTarget(node) {
    const label = node.getAttribute("data-k") || "";
    const hash = label.indexOf("#");
    return hash === -1
      ? { key: label, line: null }
      : { key: label.slice(0, hash), line: parseInt(label.slice(hash + 1), 10) };
  }

  /** The text a node should DISPLAY for the current raw value of its field. */
  /** The lines of a `lines` value, WITHOUT dropping blanks.
   *  Filtering here shifted every later item up one index while the DOM nodes kept
   *  their original `key#index`, so emptying one item silently deleted the next. */
  function linesOf(raw) {
    return String(raw == null ? "" : raw).split("\n");
  }

  function displayFor(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    if (target.line == null) return interpolate(raw);
    const lines = linesOf(raw);
    return interpolate(lines[target.line] != null ? lines[target.line] : "");
  }

  /** The text a node should EDIT: the raw value, tokens and all. */
  /** Same as displayFor, but every token escaped — the value goes to innerHTML. */
  function displayForRich(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    return interpolate(target.line == null ? raw : linesOf(raw)[target.line] || "", true);
  }

  function editableFor(target) {
    const raw = CURRENT[target.key];
    if (raw == null) return "";
    if (target.line == null) return raw;
    const lines = linesOf(raw);
    return lines[target.line] != null ? lines[target.line] : "";
  }

  /** Write `text` back into the field's raw value (splicing one line if needed).
   *  `whole` replaces the entire raw value instead, for the Escape path. */
  function applyEdit(target, text, whole) {
    if (whole !== undefined) {
      CURRENT[target.key] = whole;
      return whole;
    }
    if (target.line == null) {
      CURRENT[target.key] = text;
      return text;
    }
    const lines = linesOf(CURRENT[target.key]);
    lines[target.line] = text;
    CURRENT[target.key] = lines.join("\n");
    return CURRENT[target.key];
  }

  // A field that IS a token: changing it has to re-render every string that mentions
  // it, or the canvas shows the old brand in the footer while the live site shows the
  // new one — and the natural next move is to type the brand in by hand, which breaks
  // the token for good.
  const TOKEN_FIELDS = {
    "global.brand": "brand",
    "global.tagline": "tagline",
    "global.instagram_url": "instagram_url",
    "global.instagram_handle": "instagram_handle",
  };

  let tokenDependents = null;

  function refreshDependents(key) {
    const token = TOKEN_FIELDS[key];
    if (!token) return;
    TOKENS[token] = String(CURRENT[key] || "");
    if (tokenDependents === null) {
      tokenDependents = Object.keys(CURRENT).filter((other) =>
        /\{[a-z_]+\}/.test(String(FIELDS[other] ? FIELDS[other].raw : CURRENT[other]))
      );
    }
    tokenDependents.forEach((other) => {
      if (other !== key) refresh(other);
    });
  }

  // key -> the nodes that render it. Built once; `refresh` used to sweep every
  // <ct-t> on the page for every key, and editing a token field re-runs that for
  // each of the ~80 strings that mention it — 28 full-document scans per keystroke.
  let nodeIndex = null;

  function indexNodes() {
    nodeIndex = new Map();
    document.querySelectorAll("ct-t").forEach((node) => {
      const key = parseTarget(node).key;
      const bucket = nodeIndex.get(key);
      if (bucket) bucket.push(node);
      else nodeIndex.set(key, [node]);
    });
  }

  function nodesForKey(key) {
    if (nodeIndex === null) indexNodes();
    return nodeIndex.get(key) || [];
  }

  function refresh(key) {
    nodesForKey(key).forEach((node) => {
      if (node.hasAttribute("data-ct-editing")) return;
      const target = parseTarget(node);
      const field = FIELDS[key] || {};
      if (field.type === "rich") node.innerHTML = displayForRich(target);
      else node.textContent = displayFor(target);
      if (CURRENT[key] !== field.raw) node.setAttribute("data-ct-dirty", "");
      else node.removeAttribute("data-ct-dirty");
    });
  }

  /* ---------------- floating chrome ---------------- */

  let tip = null;
  let tipTimer = null;
  /** Put the caret at `range`, nudged off the whitespace between block elements.
   *  The raw-value swap changes the text length, so the click point can land in the
   *  gap between two paragraphs, where typing goes nowhere useful. */
  function placeCaret(range) {
    let container = range.startContainer;
    let offset = range.startOffset;
    if (container.nodeType === 3 && !container.textContent.trim()) {
      const walker = document.createTreeWalker(editing || document.body, NodeFilter.SHOW_TEXT);
      let previous = null;
      while (walker.nextNode()) {
        if (walker.currentNode === container) break;
        if (walker.currentNode.textContent.trim()) previous = walker.currentNode;
      }
      const target = previous || walker.nextNode();
      if (target && target.textContent.trim()) {
        container = target;
        offset = previous ? target.textContent.length : 0;
      }
    }
    const selection = window.getSelection();
    const placed = document.createRange();
    try {
      placed.setStart(container, Math.min(offset, (container.textContent || "").length));
      placed.collapse(true);
    } catch (_) {
      return;
    }
    selection.removeAllRanges();
    selection.addRange(placed);
  }

  // Controls the SITE owns — a click on one of these must reach the site so the cart,
  // the menu and the gallery keep working. Deliberately native-only: the editor adds
  // role="button" to elements it makes focusable, and matching those here would swallow
  // the very clicks that open their copy.
  function place(el, anchor, above) {
    const box = anchor.getBoundingClientRect();
    el.style.left = Math.max(6, box.left + window.scrollX) + "px";
    el.style.top =
      (above ? box.top + window.scrollY - el.offsetHeight - 8 : box.bottom + window.scrollY + 6) +
      "px";
  }

  function showTip(node, text) {
    hideTip();
    tip = document.createElement("div");
    tip.className = "ct-tip";
    tip.id = "ctTipLive";
    tip.setAttribute("role", "status");
    tip.setAttribute("aria-live", "polite");
    tip.textContent = text;
    document.body.appendChild(tip);
    place(tip, node, false);
  }

  function hideTip() {
    if (tipTimer) {
      window.clearTimeout(tipTimer);
      tipTimer = null;
    }
    if (tip) tip.remove();
    tip = null;
  }

  /** The bubble under the text being edited: hint, token warning and live count. */
  function showHint(node, field) {
    const length = (field.type === "rich" ? node.innerHTML : node.textContent).length;
    const parts = [];
    if (field.hint) parts.push(field.hint);
    if (/\{[a-z_]+\}/.test(String(CURRENT[parseTarget(node).key]))) {
      parts.push("Dejá los {textos entre llaves} tal cual.");
    }
    parts.push(length + " / " + field.max + " caracteres");
    showTip(node, parts.join(" · "));
    if (tip) tip.classList.toggle("is-over", length > field.max);
  }

  function currentText(node, field) {
    const text = field.type === "rich" ? node.innerHTML : node.textContent;
    return String(text).replace(/\u00a0/g, " ").trim();
  }

  /** A `rich` node is contenteditable="true", so a paste from a word processor lands its
   *  <span style> soup straight into the staged value — and worse, its <p> tags make
   *  isBlockRich() true on the next load, so a heading silently stops being editable in
   *  place. These four fields are short headings, so take the words and drop the styling
   *  here, where it can still be seen. (`plaintext-only` fields never get here: the
   *  browser already does this.) */
  function onPaste(event) {
    const node = event.currentTarget;
    const field = FIELDS[parseTarget(node).key] || {};
    if (field.type !== "rich") return;
    const data = event.clipboardData;
    if (!data) return;
    event.preventDefault();
    document.execCommand("insertText", false, data.getData("text/plain"));
  }

  function onInput(event) {
    const node = event.currentTarget;
    const target = parseTarget(node);
    const field = FIELDS[target.key] || { max: 0 };
    showHint(node, field);
    // Stage on every keystroke, not on blur: the Save button used to stay dead while
    // you typed, which reads as broken — and reloading to "fix" it threw the edit
    // away. refresh() skips the node being edited, so the caret is safe and the other
    // copies of the same string update live.
    const raw = applyEdit(target, currentText(node, field));
    refresh(target.key);
    refreshDependents(target.key);
    post({ type: "change", key: target.key, value: raw, original: field.raw });
  }

  /** Keep the text being edited out from under a phone's on-screen keyboard. */
  function keepVisible(node) {
    const view = window.visualViewport;
    const height = view ? view.height : window.innerHeight;
    const box = node.getBoundingClientRect();
    if (box.top < 8 || box.bottom > height - 8) {
      window.scrollBy({ top: box.top - height / 3, behavior: "instant" });
    }
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => {
      if (editing) keepVisible(editing);
    });
  }

  const INTERACTIVE = "a[href], button, input, select, textarea, summary";

  function isInteractive(el) {
    return Boolean(el && el.closest && el.closest(INTERACTIVE));
  }

  // Product titles, prices and descriptions come from the catalogue, not from the
  // text registry. Clicking them used to do nothing at all, which reads as broken.
  // An array, not a comma-joined string: `indexOf` on the string matched "B" inside
  // "BUTTON", "I" inside "FIGCAPTION", and so on.
  const TEXT_TAGS = ["H1","H2","H3","H4","H5","H6","P","SPAN","A","LI","STRONG","EM",
                     "BUTTON","BLOCKQUOTE","FIGCAPTION","TD","LABEL"];
  // Only these are actually catalogue data. Everything else that isn't editable in
  // place is still site copy, and saying "it comes from the catalogue" sent people
  // to a section that has nothing to do with it.
  const CATALOG_SCOPES = ".product, .shop-grid, .pdp-info, .pdp-gallery, .cart-line, .cart-lines";
  let lastExplain = 0;

  function explainNotEditable(el) {
    if (!el || !el.tagName || TEXT_TAGS.indexOf(el.tagName) === -1) return;
    if (el.closest("ct-t") || el.querySelector("ct-t")) return;
    const text = (el.textContent || "").trim();
    if (!text || text.length > 200) return;
    const now = Date.now();
    if (now - lastExplain < 1200) return;
    lastExplain = now;
    showTip(
      el,
      el.closest(CATALOG_SCOPES)
        ? "Esto sale del catálogo: el título, el precio y las fotos se editan en Productos."
        : "Este texto no se edita tocándolo. Buscalo en «Ver todos los textos» o en la lista por sección."
    );
    tipTimer = window.setTimeout(hideTip, 3600);
  }

  /* ---------------- editing ---------------- */

  let editing = null;
  // Where the user actually clicked, so the caret can be put back after the raw-value
  // swap replaces the node's contents.
  let clickPoint = null;
  // The value when the current edit began — what Escape reverts to.
  let editStartValue = null;

  /** A rich value that holds BLOCK elements — a whole editorial page. Editing that
   *  in a floating inline box over the site is where every reviewer got hurt: the
   *  toolbar and the counter render outside the canvas, the caret fights the raw-value
   *  swap, and one select-all could blank the page. Short rich values (a heading with
   *  a <br>) stay in place, which is where in-place editing actually shines. */
  function isBlockRich(field, raw) {
    return field.type === "rich" && /<(p|h2|h3|ul|ol|li)\b/i.test(String(raw || ""));
  }

  /** Swapping the rendered text for the RAW value changes how many lines the copy takes:
   *  the hero title renders as three lines and its raw value is "{tagline}", one line.
   *  Clicking the most important text on the site therefore pulled the hero 98px out from
   *  under the cursor before the caret even landed. Hold the block at the height it had
   *  while the edit lasts; it can still grow. */
  let lockedBlock = null;

  function lockHeight(node) {
    let block = node.parentElement;
    while (block && window.getComputedStyle(block).display === "inline") {
      block = block.parentElement;
    }
    if (!block) return;
    lockedBlock = block;
    block.style.minHeight = block.getBoundingClientRect().height + "px";
  }

  function releaseHeight() {
    if (lockedBlock) lockedBlock.style.minHeight = "";
    lockedBlock = null;
  }

  function startEditing(node) {
    if (editing === node) return;
    if (editing) stopEditing();

    const target = parseTarget(node);
    const field = FIELDS[target.key];
    if (!field) return;

    if (isBlockRich(field, CURRENT[target.key])) {
      post({ type: "openRich", key: target.key, value: CURRENT[target.key] });
      return;
    }

    editing = node;
    editStartValue = CURRENT[target.key];
    lockHeight(node);
    node.setAttribute("data-ct-editing", "");
    node.setAttribute("contenteditable", field.type === "rich" ? "true" : "plaintext-only");
    node.setAttribute("spellcheck", "true");

    // Swap the rendered text for the RAW value: typing over an interpolated
    // "{brand}" would otherwise bake the brand name into that string forever.
    if (field.type === "rich") node.innerHTML = editableFor(target);
    else node.textContent = editableFor(target);

    node.focus();
    if (field.type === "rich" && clickPoint) {
      // Aim for the same spot on screen: landing at character 0 meant a typo fix in
      // paragraph four was typed into paragraph one.
      const range =
        document.caretRangeFromPoint && document.caretRangeFromPoint(clickPoint.x, clickPoint.y);
      if (range && node.contains(range.startContainer)) {
        placeCaret(range);
      }
    }
    clickPoint = null;
    if (field.type !== "rich") {
      // Short copy: select it so typing replaces it. A rich body is a whole page of
      // text — selecting all of it would mean one keystroke wipes the page, so there
      // the caret just lands where the user clicked.
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }

    // On a phone the keyboard covers the bottom ~half of the screen and nothing
    // scrolled the text out from under it.
    keepVisible(node);
    showHint(node, field);
    node.addEventListener("input", onInput);
    node.addEventListener("paste", onPaste);

    post({ type: "focus", key: target.key });
  }

  function stopEditing(discard) {
    if (!editing) return;
    const node = editing;
    const target = parseTarget(node);
    const field = FIELDS[target.key] || {};

    editing = null;
    releaseHeight();
    node.removeEventListener("input", onInput);
    node.removeEventListener("paste", onPaste);
    node.removeAttribute("contenteditable");
    node.removeAttribute("data-ct-editing");
    hideTip();

    // `discard` (Escape) goes back to where THIS edit began — not to the value the
    // page was loaded with, which used to throw away edits Escape never touched.
    const raw = discard
      ? applyEdit(target, null, editStartValue)
      : applyEdit(target, currentText(node, field));
    editStartValue = null;
    refresh(target.key);
    refreshDependents(target.key);
    // Reported even when the edit was cancelled: that is how the shell learns the
    // value went back to its original and drops the pending change.
    post({ type: "change", key: target.key, value: raw, original: field.raw });
  }

  document.addEventListener(
    "click",
    (event) => {
      const node = event.target.closest ? event.target.closest("ct-t") : null;
      if (node) {
        clickPoint = { x: event.clientX, y: event.clientY };
        // preventDefault stops navigation and form submission. NOT stopPropagation:
        // the site's own handlers are delegated on `document`, so stopping the event
        // in the capture phase left the cart button and the checkout button dead —
        // and their copy only exists once those panels are open.
        event.preventDefault();
        startEditing(node);
        return;
      }
      const owner = event.target.closest ? event.target.closest("[data-ct-keys]") : null;
      // An element that only carries copy in its ATTRIBUTES (an image alt, a button's
      // aria-label) opens that copy in the panel — unless it is interactive, because
      // swallowing that click would leave the cart, the menu and the gallery dead in
      // the editor, and their copy only exists once they are open.
      if (owner && !isInteractive(event.target)) {
        event.preventDefault();
        event.stopPropagation();
        stopEditing();
        post({ type: "openKeys", keys: (owner.getAttribute("data-ct-keys") || "").split(/\s+/) });
        return;
      }
      if (editing) stopEditing();
      if (!owner) explainNotEditable(event.target);
    },
    true
  );

  document.addEventListener(
    "keydown",
    (event) => {
    if (!editing) return;
    const field = FIELDS[parseTarget(editing).key] || {};
    if (event.key === "Escape") {
      event.preventDefault();
      // The site closes its cart drawer / menu on Escape too; cancelling an edit
      // must not also close the panel the text lives in.
      event.stopPropagation();
      const target = parseTarget(editing);
      stopEditing(true);
      refresh(target.key);
      return;
    }
    if (event.key === "Enter" && field.type !== "rich") {
      event.preventDefault();
      const node = editing;
      stopEditing();
      // Closing in silence read as the editor swallowing the keystroke. `text` and
      // `lines` values DO hold newlines, but only the panel can add one, so say where to
      // go instead of just eating the Enter.
      const multiline = field.type === "text" || field.type === "lines";
      showTip(
        node,
        multiline
          ? "Listo. Acá Enter cierra la edición; para separar en renglones, editá este texto en el panel de la derecha."
          : "Listo. Enter cierra la edición: este texto va en una sola línea."
      );
      tipTimer = window.setTimeout(hideTip, 3600);
      return;
    }

    },
    true
  );

  document.addEventListener("focusout", (event) => {
    if (editing && event.target === editing) {
      // Let the browser settle the new focus target before tearing down.
      window.setTimeout(() => {
        if (editing && !editing.contains(document.activeElement)) stopEditing();
      }, 0);
    }
  });

  /* ---------------- navigation ---------------- */

  // Keep the editor alive while browsing the site inside the frame.
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest ? event.target.closest("a[href]") : null;
      if (!link || event.defaultPrevented) return;
      if (event.target.closest("ct-t")) return;  // that click opened an editor
      const url = new URL(link.getAttribute("href"), window.location.href);
      if (url.origin !== window.location.origin) {
        link.setAttribute("target", "_blank");
        return;
      }
      if (url.pathname === window.location.pathname && url.hash) return; // in-page anchor
      event.preventDefault();
      url.searchParams.set("edit", "1");
      window.location.href = url.toString();
    },
    false
  );

  /* ---------------- shell messages ---------------- */

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data || {};
    if (data.source !== "ct-shell") return;
    if (data.type === "set") {
      CURRENT[data.key] = data.value;
      refresh(data.key);
      refreshDependents(data.key);
    } else if (data.type === "highlight") {
      document.querySelectorAll("ct-t[data-ct-invalid]").forEach((node) =>
        node.removeAttribute("data-ct-invalid")
      );
      document.querySelectorAll("ct-t[aria-invalid]").forEach((node) =>
        node.removeAttribute("aria-invalid")
      );
      (data.keys || []).forEach((key) => {
        document.querySelectorAll("ct-t").forEach((node) => {
          if (parseTarget(node).key !== key) return;
          node.setAttribute("data-ct-invalid", "");
          node.setAttribute("aria-invalid", "true");
        });
      });
      const first = (data.keys || [])[0];
      const nodes = first ? document.querySelectorAll("ct-t[data-ct-invalid]") : [];
      if (nodes.length) nodes[0].scrollIntoView({ block: "center", behavior: "smooth" });
    }
  });

  /** Make every editable string reachable without a mouse, and give it a name. */
  function makeReachable() {
    indexNodes();
    document.querySelectorAll("ct-t").forEach((node) => {
      const field = FIELDS[parseTarget(node).key];
      if (!field) return;
      node.setAttribute("tabindex", "0");
      // A control that OPENS an editor — not a textbox. It only becomes one while
      // `contenteditable` is on, and then the browser reports the right role itself.
      node.setAttribute("role", "button");
      // Saved but not published: on the canvas a draft looks exactly like the live copy,
      // so nothing here said "your customers are not seeing this yet". The manifest is
      // rebuilt on every load, so this is right on a cold start AND after navigating to
      // another page inside the frame.
      if (field.hasDraft) node.setAttribute("data-ct-draft", "");
      const block = isBlockRich(field, CURRENT[parseTarget(node).key]);
      node.setAttribute(
        "aria-label",
        (block ? "Editar el texto de esta página: " : "Editar: ") +
          field.label +
          (field.hasDraft ? " (guardado, sin publicar)" : "")
      );
      if (block) node.setAttribute("data-ct-block", "");
      else node.setAttribute("aria-describedby", "ctTipLive");
    });
    // Elements whose copy lives only in an attribute (an image's alt) had a badge
    // that only hover could raise, so it could never be reached by keyboard or touch.
    document.querySelectorAll("[data-ct-keys]").forEach((node) => {
      if (node.matches(INTERACTIVE) || node.closest("head")) return;
      if (node.hasAttribute("tabindex")) return;
      node.setAttribute("tabindex", "0");
      node.setAttribute("role", "button");
      node.setAttribute("aria-label", "Editar los textos de este elemento");
    });
  }

  document.addEventListener("keydown", (event) => {
    if (editing) return;
    // The capture-phase handler above already consumed this key to COMMIT an edit;
    // the element keeps focus (it is focusable now), so without this the same Enter
    // reopened it with everything selected and the next keystroke wiped the field.
    if (event.defaultPrevented) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    const node = document.activeElement;
    if (!node) return;
    if (node.tagName === "CT-T") {
      event.preventDefault();
      startEditing(node);
      return;
    }
    if (node.hasAttribute && node.hasAttribute("data-ct-keys")) {
      event.preventDefault();
      post({ type: "openKeys", keys: (node.getAttribute("data-ct-keys") || "").split(/\s+/) });
    }
  });

  makeReachable();
  post({ type: "ready", path: window.location.pathname, manifest: MANIFEST });
})();
