// GLÜCK admin — uploader, drag-to-reorder (touch-friendly), upload progress.
// Vanilla JS, no dependencies. Enhances a plain multipart form; if JS is off the
// form still works (the server appends new files and keeps existing order).

(function () {
  "use strict";

  /* ---- Reusable vertical-list sortable (Pointer Events, works on touch) ----
     Dragging starts only from a handle (so the list still scrolls on a phone).
     The handle carries `touch-action: none` so the gesture doesn't scroll. */
  function getDragAfter(list, sel, y, dragging) {
    let result = null;
    let closest = -Infinity;
    list.querySelectorAll(sel).forEach((el) => {
      if (el === dragging) return;
      const box = el.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest) {
        closest = offset;
        result = el;
      }
    });
    return result;
  }

  function makeSortable(list, itemSelector, handleSelector, onChange) {
    let dragging = null;
    list.addEventListener("pointerdown", (e) => {
      const handle = e.target.closest(handleSelector);
      if (!handle || !list.contains(handle)) return;
      dragging = e.target.closest(itemSelector);
      if (!dragging) return;
      dragging.classList.add("dragging");
      try {
        handle.setPointerCapture(e.pointerId);
      } catch (_) {}
      e.preventDefault();
    });
    list.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      e.preventDefault();
      const after = getDragAfter(list, itemSelector, e.clientY, dragging);
      if (after == null) list.appendChild(dragging);
      else list.insertBefore(dragging, after);
      // Auto-scroll near the top/bottom edge so long lists are draggable on a phone.
      const margin = 70;
      if (e.clientY < margin) window.scrollBy(0, -14);
      else if (e.clientY > window.innerHeight - margin) window.scrollBy(0, 14);
    });
    const end = () => {
      if (!dragging) return;
      dragging.classList.remove("dragging");
      dragging = null;
      if (onChange) onChange();
    };
    list.addEventListener("pointerup", end);
    list.addEventListener("pointercancel", end);
  }

  /* ---- Product list: drag rows to reorder, persist via JSON ---- */
  const list = document.getElementById("admList");
  if (list) {
    makeSortable(list, ".adm-row", ".adm-handle", () => {
      const order = Array.from(list.querySelectorAll(".adm-row")).map((r) => r.dataset.id);
      fetch(list.dataset.reorderUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order: order }),
      }).catch(() => {});
    });
  }

  /* ---- Product form: media uploader + ordering + upload progress ---- */
  const form = document.getElementById("productForm");
  if (!form) return;

  const fileInput = document.getElementById("fileInput");
  const tiles = document.getElementById("tiles");
  const dropzone = document.getElementById("dropzone");
  const orderField = document.getElementById("orderField");

  let counter = 0;
  const newFiles = new Map(); // localId -> File

  const isVideo = (file) =>
    (file.type || "").startsWith("video") ||
    /\.(mp4|mov|m4v|webm|avi|mkv|3gp|ogv|mpg|mpeg)$/i.test(file.name);

  function makeNewTile(id, file) {
    const li = document.createElement("li");
    li.className = "tile";
    li.dataset.token = "new:" + id;

    const handle = document.createElement("span");
    handle.className = "tile-handle";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "⋮⋮";
    li.appendChild(handle);

    const thumb = document.createElement("div");
    thumb.className = "tile-thumb";
    if (isVideo(file)) {
      thumb.classList.add("is-video");
      thumb.textContent = "▶";
    } else {
      const img = document.createElement("img");
      const url = URL.createObjectURL(file);
      img.src = url;
      img.onload = () => URL.revokeObjectURL(url);
      thumb.appendChild(img);
    }
    li.appendChild(thumb);

    const name = document.createElement("span");
    name.className = "tile-name";
    name.textContent = file.name;
    li.appendChild(name);

    const cover = document.createElement("span");
    cover.className = "tile-cover";
    cover.textContent = "Portada";
    li.appendChild(cover);

    const makeCover = document.createElement("button");
    makeCover.type = "button";
    makeCover.className = "tile-makecover";
    makeCover.setAttribute("aria-label", "Hacer portada");
    makeCover.title = "Hacer portada";
    makeCover.textContent = "★";
    li.appendChild(makeCover);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "tile-remove";
    remove.setAttribute("aria-label", "Quitar");
    remove.textContent = "×";
    li.appendChild(remove);

    return li;
  }

  function addFiles(fileList) {
    Array.from(fileList).forEach((file) => {
      const id = "f" + counter++;
      newFiles.set(id, file);
      tiles.appendChild(makeNewTile(id, file));
    });
  }

  // Tile actions (make-cover / remove) via delegation.
  tiles.addEventListener("click", (e) => {
    const makeCover = e.target.closest(".tile-makecover");
    if (makeCover) {
      const li = makeCover.closest(".tile");
      if (li && tiles.firstElementChild !== li) tiles.insertBefore(li, tiles.firstElementChild);
      return;
    }
    const btn = e.target.closest(".tile-remove");
    if (!btn) return;
    const li = btn.closest(".tile");
    const token = li.dataset.token || "";
    if (
      token.indexOf("existing:") === 0 &&
      !window.confirm("¿Quitar esta foto/video del producto? Se borrará al guardar los cambios.")
    ) {
      return;
    }
    if (token.indexOf("new:") === 0) newFiles.delete(token.slice(4));
    li.remove();
  });

  if (fileInput) {
    fileInput.addEventListener("change", () => {
      addFiles(fileInput.files);
      fileInput.value = ""; // JS owns the files now; rebuilt on submit
    });
  }

  if (dropzone) {
    ["dragover", "dragenter"].forEach((ev) =>
      dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        dropzone.classList.add("over");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        dropzone.classList.remove("over");
      })
    );
    dropzone.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });
  }

  makeSortable(tiles, ".tile", ".tile-handle", null);

  /* ---- Submit: align new files to their `new:<index>` tokens, then upload ---- */
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    // Let the browser flag a missing title etc. WITHOUT losing the queued files.
    if (!form.reportValidity()) return;

    const dt = new DataTransfer();
    const tokens = [];
    let newIndex = 0;
    tiles.querySelectorAll(".tile").forEach((li) => {
      const token = li.dataset.token || "";
      if (token.indexOf("existing:") === 0) {
        tokens.push(token);
      } else if (token.indexOf("new:") === 0) {
        const file = newFiles.get(token.slice(4));
        if (file) {
          dt.items.add(file);
          tokens.push("new:" + newIndex);
          newIndex++;
        }
      }
    });
    if (fileInput) fileInput.files = dt.files;
    orderField.value = JSON.stringify(tokens);

    // Where the bytes cannot travel through the app (a serverless request body is
    // capped at 4.5 MB, and a phone photo clears that on its own), each file goes
    // straight to object storage first and the form posts only the pathnames.
    if (form.dataset.uploadTokenUrl) {
      uploadDirect(Array.from(dt.files)).then(uploadForm).catch((err) => {
        // Put the form back the way it was: leaving the overlay up would look like a
        // hang, and leaving `uploading` set would keep warning on every navigation.
        uploading = false;
        const overlay = document.getElementById("admOverlay");
        if (overlay) overlay.classList.remove("show");
        window.alert(err && err.message ? err.message : "No pudimos subir los archivos.");
      });
    } else {
      uploadForm();
    }
  });

  /* ---- Direct-to-storage upload (bypasses the request body limit) ---- */

  const uploadedField = document.getElementById("uploadedField");

  async function uploadDirect(files) {
    const overlay = document.getElementById("admOverlay");
    const bar = document.getElementById("admBar");
    const text = document.getElementById("admText");
    if (overlay) overlay.classList.add("show");
    if (text) text.textContent = "Subiendo archivos…";
    uploading = true;

    const uploaded = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const ticket = await fetch(form.dataset.uploadTokenUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name }),
      }).then((r) => (r.ok ? r.json() : r.json().then((b) => Promise.reject(new Error(b.error || "No pudimos preparar la subida.")))));

      if (file.size > ticket.maxBytes) {
        throw new Error("«" + file.name + "» supera el tamaño máximo permitido.");
      }

      const put = await fetch(ticket.uploadUrl, {
        method: "PUT",
        headers: {
          authorization: "Bearer " + ticket.token,
          "x-api-version": "12",
          "x-content-type": file.type || "application/octet-stream",
          "x-vercel-blob-access": "public",
        },
        body: file,
      });
      if (!put.ok) throw new Error("No pudimos subir «" + file.name + "».");

      uploaded.push({ pathname: ticket.pathname, filename: file.name });
      if (bar) bar.style.width = Math.round(((i + 1) / files.length) * 100) + "%";
    }

    uploadedField.value = JSON.stringify(uploaded);
    // The bytes are already stored; posting them again would hit the very limit this
    // whole path exists to avoid.
    if (fileInput) fileInput.value = "";
    if (text) text.textContent = "Optimizando fotos y videos…";
  }

  // Warn before leaving mid-upload so the owner doesn't lose queued files.
  let uploading = false;
  window.addEventListener("beforeunload", (e) => {
    if (uploading) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  function uploadForm() {
    const overlay = document.getElementById("admOverlay");
    const bar = document.getElementById("admBar");
    const text = document.getElementById("admText");
    if (overlay) overlay.classList.add("show");
    uploading = true;

    const fail = (message) => {
      uploading = false;
      if (overlay) overlay.classList.remove("show");
      window.alert(message);
    };

    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    // Long enough for a video transcode, short enough to surface a dead connection.
    xhr.timeout = 10 * 60 * 1000;
    xhr.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable && bar) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + "%";
    });
    xhr.upload.addEventListener("load", () => {
      if (bar) bar.style.width = "100%";
      if (text) text.textContent = "Optimizando fotos y videos…";
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 400) {
        uploading = false; // about to navigate; don't trigger the beforeunload guard
        window.location.href = xhr.responseURL || form.dataset.listUrl || "/admin/";
      } else if (xhr.status === 400) {
        uploading = false;
        document.open();
        document.write(xhr.responseText);
        document.close();
      } else if (xhr.status === 413) {
        fail("El archivo es demasiado grande (máximo 200 MB). Probá con un video más corto o de menor calidad.");
      } else {
        fail("Hubo un error al guardar. Probá de nuevo.");
      }
    });
    xhr.addEventListener("error", () => fail("Error de conexión. Revisá tu internet y probá de nuevo."));
    xhr.addEventListener("timeout", () =>
      fail("La subida tardó demasiado. Probá con un archivo más liviano o mejor conexión.")
    );
    xhr.send(new FormData(form));
  }
})();
