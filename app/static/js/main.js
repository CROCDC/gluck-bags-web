// GLÜCK — landing interactions

(function () {
  "use strict";

  /* ---- Background videos: lazy load + play/pause based on viewport ----
     The videos are heavy (several MB) and decoding them in the background while
     scrolling causes jank. Strategy: don't download anything until the section
     approaches the viewport; only then inject the sources, play, and pause as
     soon as it leaves the screen to free up the decoder. */
  const lazyVideos = document.querySelectorAll(".js-lazy-video");

  const sourcesFor = (video) => {
    const d = video.dataset;
    if (d.desktopWebm) {
      // "materia" video: desktop (landscape) or mobile (vertical) pair based on viewport.
      const isDesktop = window.matchMedia("(min-width: 760px)").matches;
      return [
        [isDesktop ? d.desktopWebm : d.mobileWebm, "video/webm"],
        [isDesktop ? d.desktopMp4 : d.mobileMp4, "video/mp4"],
      ];
    }
    // Generic video with a single webm/mp4 source.
    return [
      [d.srcWebm, "video/webm"],
      [d.srcMp4, "video/mp4"],
    ];
  };

  const loadVideoSources = (video) => {
    if (video.dataset.loaded) return;
    video.dataset.loaded = "1";
    // Poster is deferred (data-poster) so its ~150-180KB JPEG doesn't load
    // eagerly and steal bandwidth from the initial paint. Set it now — the
    // observer fires ~200px before the section, so it's painted in time.
    if (video.dataset.poster && !video.poster) {
      video.poster = video.dataset.poster;
    }
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
      // Start loading a bit before it enters, so the frozen poster isn't shown.
      { rootMargin: "200px 0px" }
    );
    lazyVideos.forEach((v) => videoIO.observe(v));
  } else {
    // Without IntersectionObserver: load and play everything.
    lazyVideos.forEach((v) => {
      loadVideoSources(v);
      const p = v.play();
      if (p && p.catch) p.catch(() => {});
    });
  }

  const header = document.getElementById("header");
  const toggle = document.getElementById("menuToggle");
  const menu = document.getElementById("mobileMenu");

  /* ---- Sticky header: solid background after scrolling past the hero edge.
     Uses an IntersectionObserver on a top sentinel instead of reading
     window.scrollY in a scroll handler — reading geometry on every scroll frame
     forces a synchronous reflow (layout thrashing / "reprocesamiento forzado"). */
  const sentinel = document.createElement("div");
  sentinel.setAttribute("aria-hidden", "true");
  sentinel.style.cssText =
    "position:absolute;top:0;left:0;width:1px;height:40px;pointer-events:none;";
  document.body.prepend(sentinel);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(
      ([entry]) => header.classList.toggle("scrolled", !entry.isIntersecting),
      { threshold: 0 }
    ).observe(sentinel);
  } else {
    header.classList.add("scrolled");
  }

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

  /* ---- PDP gallery: thumbnail strip drives the snap-scroll track ----
     The track scrolls/swipes natively (scroll-snap, no JS needed); the thumbs
     jump between slides and mirror the visible one. Progressive enhancement:
     without JS the thumbs are inert but every slide stays reachable by swipe. */
  const galleryTrack = document.querySelector("[data-gallery]");
  const galleryThumbs = Array.from(document.querySelectorAll("[data-gallery-thumb]"));
  if (galleryTrack && galleryThumbs.length) {
    const slides = Array.from(galleryTrack.children);
    const setCurrent = (index) =>
      galleryThumbs.forEach((t, i) => t.classList.toggle("is-current", i === index));

    galleryThumbs.forEach((thumb, index) => {
      thumb.addEventListener("click", () => {
        const slide = slides[index];
        if (slide) galleryTrack.scrollTo({ left: slide.offsetLeft, behavior: "smooth" });
        setCurrent(index);
      });
    });

    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) setCurrent(slides.indexOf(entry.target));
          });
        },
        { root: galleryTrack, threshold: 0.6 }
      );
      slides.forEach((s) => io.observe(s));
    }
  }

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
