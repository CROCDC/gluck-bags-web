// GLÜCK admin — "Textos" section. Vanilla JS, no dependencies.
//
// Everything here is progressive enhancement: with JS off the editor still saves,
// restores and publishes (plain form posts) and the preview still shows the page in
// the iframe. What JS adds is the character counters, the field filter and the
// preview format switching.

(function () {
  "use strict";

  /* ================= editor ================= */

  function initEditor() {
    const form = document.getElementById("contentForm");
    if (!form) return;

    // --- character counters ---
    form.querySelectorAll("[data-ct-input]").forEach((input) => {
      const foot = input.parentElement.querySelector("[data-ct-counter]");
      if (!foot) return;
      const max = parseInt(foot.dataset.max, 10) || 0;
      const render = () => {
        const len = input.value.length;
        foot.textContent = max ? len + " / " + max : String(len);
        foot.classList.toggle("is-over", max > 0 && len > max);
      };
      input.addEventListener("input", render);
      render();
    });

    // --- destructive actions ask first ---
    form.querySelectorAll("[data-ct-confirm]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        if (!window.confirm(btn.dataset.ctConfirm)) e.preventDefault();
      });
    });

    // --- filter ---
    const wrap = document.querySelector("[data-ct-filter-wrap]");
    const filter = document.querySelector("[data-ct-filter]");
    if (!wrap || !filter) return;
    wrap.hidden = false; // only useful with JS, so the markup ships hidden

    const items = Array.from(form.querySelectorAll("[data-ct-item]"));
    const sections = Array.from(form.querySelectorAll("[data-ct-section]"));
    const noResults = form.querySelector("[data-ct-no-results]");

    function apply() {
      const q = filter.value.trim().toLowerCase();
      let shown = 0;
      items.forEach((item) => {
        // Match the label, the key and the current value, so both "Título" and
        // the copy itself find the field.
        const hay = (item.dataset.ctSearch || "") + " " + currentValue(item);
        const hit = !q || hay.indexOf(q) !== -1;
        item.hidden = !hit;
        if (hit) shown++;
      });
      sections.forEach((sec) => {
        sec.hidden = !sec.querySelector("[data-ct-item]:not([hidden])");
      });
      if (noResults) noResults.hidden = shown !== 0;
    }

    function currentValue(item) {
      const input = item.querySelector("[data-ct-input]");
      return input ? input.value.toLowerCase() : "";
    }

    filter.addEventListener("input", apply);
    const clear = document.querySelector("[data-ct-filter-clear]");
    if (clear) {
      clear.addEventListener("click", () => {
        filter.value = "";
        apply();
        filter.focus();
      });
    }
  }

  /* ================= preview ================= */

  function initPreview() {
    const root = document.querySelector("[data-ct-preview]");
    if (!root) return;

    const stage = root.querySelector("[data-ct-stage]");
    const frame = root.querySelector("[data-ct-frame]");
    const iframe = root.querySelector("[data-ct-iframe]");
    const cards = root.querySelector("[data-ct-cards]");
    const buttons = Array.from(root.querySelectorAll("[data-ct-format]"));

    function fitDevice(width, height) {
      const available = stage.clientWidth - 32; // the stage's own padding
      const scale = Math.min(1, available / width);
      frame.style.width = width + "px";
      frame.style.height = height + "px";
      frame.style.transform = "scale(" + scale + ")";
      // transform doesn't affect layout, so the stage needs the scaled height.
      stage.style.height = height * scale + 32 + "px";
    }

    function showDevice(btn) {
      stage.hidden = false;
      cards.hidden = true;
      fitDevice(parseInt(btn.dataset.width, 10), parseInt(btn.dataset.height, 10));
    }

    function showCard(kind) {
      stage.hidden = true;
      cards.hidden = false;
      cards.querySelectorAll("[data-ct-card-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.ctCardPanel !== kind;
      });
      fillCards();
    }

    function select(btn) {
      buttons.forEach((b) => {
        const on = b === btn;
        b.classList.toggle("is-current", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      if (btn.dataset.ctFormat === "device") showDevice(btn);
      else showCard(btn.dataset.card);
    }

    buttons.forEach((btn) => btn.addEventListener("click", () => select(btn)));

    // --- read the previewed page's own metadata ---
    // Same-origin iframe, so we can read exactly the tags the page emitted. That
    // keeps these cards honest: no second implementation of the meta logic here.
    function readMeta() {
      let doc = null;
      try {
        doc = iframe.contentDocument;
      } catch (_) {
        return null;
      }
      if (!doc || !doc.head) return null;
      const pick = (sel, attr) => {
        const el = doc.head.querySelector(sel);
        return el ? (el.getAttribute(attr || "content") || "").trim() : "";
      };
      return {
        title: (doc.title || "").trim(),
        description: pick('meta[name="description"]'),
        ogTitle: pick('meta[property="og:title"]'),
        ogDescription: pick('meta[property="og:description"]'),
        ogImage: pick('meta[property="og:image"]'),
        ogUrl: pick('meta[property="og:url"]'),
        twTitle: pick('meta[name="twitter:title"]'),
        twDescription: pick('meta[name="twitter:description"]'),
        twImage: pick('meta[name="twitter:image"]'),
        canonical: pick('link[rel="canonical"]', "href"),
      };
    }

    function setText(sel, value) {
      const el = root.querySelector(sel);
      if (el) el.textContent = value || "";
    }

    function setImage(sel, url) {
      const el = root.querySelector(sel);
      if (!el) return;
      if (url) {
        el.src = url;
        el.hidden = false;
      } else {
        el.removeAttribute("src");
        el.hidden = true;
      }
    }

    function fillCards() {
      const meta = readMeta();
      const fallback = root.querySelector("[data-ct-card-fallback]");
      if (!meta) {
        if (fallback) fallback.hidden = false;
        return;
      }
      if (fallback) fallback.hidden = true;

      const pageUrl = meta.canonical || meta.ogUrl || root.dataset.siteUrl + root.dataset.previewPath;
      let host = pageUrl;
      try {
        host = new URL(pageUrl).host;
      } catch (_) {}

      // Google
      setText("[data-ct-serp-url]", pageUrl);
      setText("[data-ct-serp-title]", meta.title);
      setText("[data-ct-serp-desc]", meta.description);

      // WhatsApp / Facebook (Open Graph)
      setText("[data-ct-og-title]", meta.ogTitle || meta.title);
      setText("[data-ct-og-desc]", meta.ogDescription || meta.description);
      setText("[data-ct-og-host]", host);
      setText("[data-ct-og-url]", pageUrl);
      setImage("[data-ct-og-image]", meta.ogImage);

      // Twitter / X
      setText("[data-ct-tw-host]", host);
      setText("[data-ct-tw-title]", meta.twTitle || meta.ogTitle || meta.title);
      setText("[data-ct-tw-desc]", meta.twDescription || meta.ogDescription || meta.description);
      setImage("[data-ct-tw-image]", meta.twImage || meta.ogImage);
    }

    iframe.addEventListener("load", () => {
      if (!cards.hidden) fillCards();
    });

    const reload = root.querySelector("[data-ct-reload]");
    if (reload) {
      reload.addEventListener("click", () => {
        // Cache-bust so an edited draft is never served from the bfcache.
        const base = root.dataset.previewUrl;
        iframe.src = base + "&_=" + Date.now();
      });
    }

    window.addEventListener("resize", () => {
      const current = buttons.find((b) => b.classList.contains("is-current"));
      if (current && current.dataset.ctFormat === "device") showDevice(current);
    });

    const first = buttons.find((b) => b.classList.contains("is-current")) || buttons[0];
    if (first) select(first);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initEditor();
      initPreview();
    });
  } else {
    initEditor();
    initPreview();
  }
})();
