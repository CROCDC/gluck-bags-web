// GLÜCK — carrito (headless POC, Fase 3a)
// Drawer + página /carrito. Toda mutación devuelve el carrito completo desde el
// backend, así que el front sólo renderiza lo que recibe (no reconcilia estado).

(function () {
  "use strict";

  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const drawer = document.getElementById("cartDrawer");
  if (!drawer) return; // sólo en las páginas públicas que incluyen el drawer

  const toggle = document.getElementById("cartToggle");
  const body = drawer.querySelector("[data-cart-body]");
  const foot = drawer.querySelector("[data-cart-foot]");
  let lastFocus = null;

  /* ---- HTTP ---- */
  async function api(path, payload) {
    const opts = { method: payload ? "POST" : "GET", headers: {} };
    if (payload) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(payload);
    }
    let data = null;
    let ok = false;
    let status = 0;
    try {
      const res = await fetch(path, opts);
      ok = res.ok;
      status = res.status;
      data = await res.json();
    } catch (e) {
      /* network error → data stays null */
    }
    return { ok, status, data };
  }

  /* ---- helpers ---- */
  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  function updateBadges(count) {
    $$("[data-cart-count]").forEach((el) => {
      el.textContent = count;
      if (count > 0) el.removeAttribute("hidden");
      else el.setAttribute("hidden", "");
    });
  }

  /* ---- drawer rendering ---- */
  function lineHTML(it) {
    // Escape EVERY interpolated value (not just title): it.image comes from the
    // catalogue source's cover URL, which under CATALOG_SOURCE=tiendanube is external
    // (mirrored Tienda Nube data). An unescaped value in an attribute could break out
    // of src="/href=" — escapeHtml keeps it inert while staying a valid URL.
    const url = escapeHtml(it.url);
    return (
      '<li class="cart-line" data-cart-line="' + it.id + '">' +
      '<a class="cart-line-media" href="' + url + '">' +
      (it.image ? '<img src="' + escapeHtml(it.image) + '" alt="" width="72" height="90" loading="lazy">' : "") +
      "</a>" +
      '<div class="cart-line-info">' +
      '<a class="cart-line-title" href="' + url + '">' + escapeHtml(it.title) + "</a>" +
      '<span class="cart-line-price">' + it.price_formatted + "</span>" +
      '<div class="qty-stepper">' +
      '<button class="qty-btn" data-qty-dec aria-label="Quitar uno">−</button>' +
      '<span class="qty-value" data-qty-value>' + it.qty + "</span>" +
      '<button class="qty-btn" data-qty-inc aria-label="Agregar uno">+</button>' +
      "</div></div>" +
      '<div class="cart-line-end">' +
      '<span class="cart-line-total" data-line-total>' + it.line_total_formatted + "</span>" +
      '<button class="cart-line-remove" data-cart-remove aria-label="Quitar">Quitar</button>' +
      "</div></li>"
    );
  }

  function renderDrawer(cart) {
    if (!cart.items.length) {
      body.innerHTML =
        '<div class="cart-empty"><p>Tu carrito está vacío.</p>' +
        '<a class="btn btn-dark" href="/#shop" data-cart-close>Ver bolsos</a></div>';
      if (foot) foot.hidden = true;
      return;
    }
    body.innerHTML = '<ul class="cart-lines">' + cart.items.map(lineHTML).join("") + "</ul>";
    if (foot) {
      foot.hidden = false;
      const sub = foot.querySelector("[data-cart-subtotal]");
      if (sub) sub.textContent = cart.subtotal_formatted;
      hideFeedback(foot);
    }
  }

  /* ---- /carrito page: update in place ---- */
  function syncPage(cart) {
    const pageLines = document.querySelector(".cart-page [data-cart-lines]");
    if (!pageLines) return;
    if (!cart.items.length) {
      window.location.reload(); // fall back to the server-rendered empty state
      return;
    }
    const byId = new Map(cart.items.map((it) => [String(it.id), it]));
    $$("[data-cart-line]", pageLines).forEach((li) => {
      const it = byId.get(li.getAttribute("data-cart-line"));
      if (!it) {
        li.remove();
        return;
      }
      const qv = li.querySelector("[data-qty-value]");
      if (qv) qv.textContent = it.qty;
      const lt = li.querySelector("[data-line-total]");
      if (lt) lt.textContent = it.line_total_formatted;
    });
    const psub = document.querySelector(".cart-summary [data-cart-subtotal]");
    if (psub) psub.textContent = cart.subtotal_formatted;
  }

  function applyCart(cart) {
    if (!cart) return;
    updateBadges(cart.count);
    renderDrawer(cart);
    syncPage(cart);
  }

  /* ---- open / close ---- */
  function openDrawer() {
    lastFocus = document.activeElement;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("cart-open");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    const closeBtn = drawer.querySelector(".cart-close");
    if (closeBtn) closeBtn.focus();
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("cart-open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  if (toggle) {
    toggle.addEventListener("click", async () => {
      openDrawer();
      const r = await api("/api/cart");
      applyCart(r.data);
    });
  }

  /* ---- feedback (checkout not-ready / add errors) ---- */
  function feedbackEl(near) {
    const scope =
      (near && near.closest("[data-cart-foot], .cart-summary")) ||
      foot ||
      document.querySelector(".cart-summary");
    return scope ? scope.querySelector("[data-cart-feedback]") : null;
  }
  function showFeedback(near, msg) {
    const el = feedbackEl(near);
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }
  function hideFeedback(scope) {
    const el = scope ? scope.querySelector("[data-cart-feedback]") : null;
    if (el) el.hidden = true;
  }

  /* ---- delegated interactions (drawer + page) ---- */
  document.addEventListener("click", async (e) => {
    const closer = e.target.closest("[data-cart-close]");
    if (closer && drawer.contains(closer)) {
      if (closer.tagName !== "A") e.preventDefault();
      closeDrawer();
      return;
    }

    const add = e.target.closest("[data-add-to-cart]");
    if (add) {
      e.preventDefault();
      add.disabled = true;
      const r = await api("/api/cart/add", {
        product_id: add.getAttribute("data-add-to-cart"),
        qty: 1,
      });
      add.disabled = false;
      if (r.ok && r.data) {
        applyCart(r.data);
        openDrawer();
      } else {
        showFeedback(add, (r.data && r.data.error) || "No pudimos agregar el producto.");
      }
      return;
    }

    const line = e.target.closest("[data-cart-line]");
    if (line) {
      const id = line.getAttribute("data-cart-line");
      const cur = parseInt(
        (line.querySelector("[data-qty-value]") || {}).textContent,
        10
      ) || 0;
      if (e.target.closest("[data-qty-inc]")) {
        applyCart((await api("/api/cart/update", { product_id: id, qty: cur + 1 })).data);
        return;
      }
      if (e.target.closest("[data-qty-dec]")) {
        applyCart((await api("/api/cart/update", { product_id: id, qty: cur - 1 })).data);
        return;
      }
      if (e.target.closest("[data-cart-remove]")) {
        applyCart((await api("/api/cart/remove", { product_id: id })).data);
        return;
      }
    }

    const checkout = e.target.closest("[data-cart-checkout]");
    if (checkout) {
      e.preventDefault();
      checkout.disabled = true;
      const r = await api("/checkout", {});
      checkout.disabled = false;
      const d = r.data || {};
      if (d.ready && d.redirect_url) {
        window.location.href = d.redirect_url; // Fase 3b: redirect a Tienda Nube
        return;
      }
      showFeedback(
        checkout,
        d.message || "No pudimos iniciar el checkout. Probá de nuevo en un momento."
      );
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
  });
})();
