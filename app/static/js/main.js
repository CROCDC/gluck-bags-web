// GLÜCK — landing interactions

(function () {
  "use strict";

  /* ---- Videos de fondo: lazy load + play/pause según viewport ----
     Los videos son pesados (varios MB) y decodificarlos de fondo mientras se
     hace scroll genera jank. Estrategia: no descargar nada hasta que la sección
     se acerca al viewport; recién ahí inyectar las fuentes, reproducir, y pausar
     en cuanto sale de pantalla para liberar al decodificador. */
  const lazyVideos = document.querySelectorAll(".js-lazy-video");

  const sourcesFor = (video) => {
    const d = video.dataset;
    if (d.desktopWebm) {
      // Video "materia": par desktop (landscape) o mobile (vertical) según viewport.
      const isDesktop = window.matchMedia("(min-width: 760px)").matches;
      return [
        [isDesktop ? d.desktopWebm : d.mobileWebm, "video/webm"],
        [isDesktop ? d.desktopMp4 : d.mobileMp4, "video/mp4"],
      ];
    }
    // Video genérico con una sola fuente webm/mp4.
    return [
      [d.srcWebm, "video/webm"],
      [d.srcMp4, "video/mp4"],
    ];
  };

  const loadVideoSources = (video) => {
    if (video.dataset.loaded) return;
    video.dataset.loaded = "1";
    const html = sourcesFor(video)
      .filter(([src]) => src)
      .map(([src, type]) => '<source src="' + src + '" type="' + type + '">')
      .join("");
    video.insertAdjacentHTML("beforeend", html);
    video.load();
  };

  if ("IntersectionObserver" in window && lazyVideos.length) {
    const videoIO = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const video = entry.target;
          if (entry.isIntersecting) {
            loadVideoSources(video);
            const p = video.play();
            if (p && p.catch) p.catch(() => {});
          } else if (!video.paused) {
            video.pause();
          }
        });
      },
      // Empezamos a cargar un poco antes de que entre, para que no se vea el poster congelado.
      { rootMargin: "200px 0px" }
    );
    lazyVideos.forEach((v) => videoIO.observe(v));
  } else {
    // Sin IntersectionObserver: cargar y reproducir todo.
    lazyVideos.forEach((v) => {
      loadVideoSources(v);
      const p = v.play();
      if (p && p.catch) p.catch(() => {});
    });
  }

  const header = document.getElementById("header");
  const toggle = document.getElementById("menuToggle");
  const menu = document.getElementById("mobileMenu");

  /* ---- Sticky header: solid background after scrolling past the hero edge ---- */
  const onScroll = () => {
    if (window.scrollY > 40) header.classList.add("scrolled");
    else header.classList.remove("scrolled");
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---- Mobile menu toggle ---- */
  const closeMenu = () => {
    menu.hidden = true;
    toggle.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
  };
  const openMenu = () => {
    menu.hidden = false;
    toggle.classList.add("open");
    toggle.setAttribute("aria-expanded", "true");
  };

  toggle.addEventListener("click", () => {
    if (menu.hidden) openMenu();
    else closeMenu();
  });
  menu.querySelectorAll("a").forEach((a) => a.addEventListener("click", closeMenu));

  /* ---- Reveal on scroll ---- */
  const reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }
})();
