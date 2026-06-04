"""Tests de performance del sitio.

Abren la landing en Chromium headless (Playwright) y miden métricas reales del
navegador: Core Web Vitals (LCP, CLS), tiempos de Navigation Timing, el peso de
la página y el comportamiento de carga de recursos. Cada test compara contra un
*presupuesto de performance* (performance budget); si el sitio se vuelve más
lento, más pesado o rompe un recurso, el test falla y lo vemos antes de deployar.

Correr solo estos tests:
    pytest tests/test_performance.py -v

Los umbrales se miden en local sin throttling. Ajustalos si querés ser más/menos
estricto. Referencia Google Web Vitals ("good"): LCP < 2500ms, CLS < 0.1.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page

# --- Presupuestos de performance ---------------------------------------------
# Con las fuentes auto-hosteadas el LCP (texto del hero) cae a ~400-900ms en
# local; dejamos headroom para variar entre máquinas/CI sin volverse flaky.

LCP_BUDGET_MS = 1500          # Largest Contentful Paint
CLS_BUDGET = 0.1             # Cumulative Layout Shift
TTFB_BUDGET_MS = 800          # Time To First Byte
DOM_CONTENT_LOADED_BUDGET_MS = 2000
LOAD_BUDGET_MS = 4000
PAGE_WEIGHT_BUDGET_KB = 1500  # peso transferido de la primera carga (sin videos)


@pytest.fixture(scope="session")
def page(browser: Browser, live_server: str) -> Iterator[Page]:
    """Página de Chromium ya navegada al sitio, con los recursos en caché tibia.

    Hacemos una primera visita de "warm-up" y luego un reload para medir, de modo
    que la medición refleje la performance del *sitio* sin la varianza de red de
    la primera descarga. (El cold-start se cubre aparte abajo.)
    """
    page = browser.new_page()
    page.goto(live_server, wait_until="load")  # warm-up
    page.goto(live_server, wait_until="load")  # carga medida
    page.wait_for_timeout(500)
    yield page
    page.close()


def _navigation_timing(page: Page) -> dict[str, float]:
    """Lee las métricas de Navigation Timing (en ms, relativas a navigationStart)."""
    return page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0];
            return {
                ttfb: nav.responseStart,
                domContentLoaded: nav.domContentLoadedEventEnd,
                load: nav.loadEventEnd,
                transferSizeKb: performance
                    .getEntriesByType('resource')
                    .reduce((acc, r) => acc + (r.transferSize || 0), nav.transferSize || 0) / 1024,
            };
        }"""
    )


def _largest_contentful_paint(page: Page) -> float:
    """Devuelve el LCP en ms observado por PerformanceObserver."""
    return page.evaluate(
        """() => new Promise((resolve) => {
            let lcp = 0;
            new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    lcp = entry.startTime;
                }
            }).observe({ type: 'largest-contentful-paint', buffered: true });
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(lcp)));
        })"""
    )


def _cumulative_layout_shift(page: Page) -> float:
    """Devuelve el CLS acumulado observado por PerformanceObserver."""
    return page.evaluate(
        """() => new Promise((resolve) => {
            let cls = 0;
            new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (!entry.hadRecentInput) cls += entry.value;
                }
            }).observe({ type: 'layout-shift', buffered: true });
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(cls)));
        })"""
    )


# --- Tests de métricas (carga tibia) -----------------------------------------


def test_time_to_first_byte(page: Page) -> None:
    ttfb = _navigation_timing(page)["ttfb"]
    assert ttfb < TTFB_BUDGET_MS, f"TTFB {ttfb:.0f}ms supera el presupuesto de {TTFB_BUDGET_MS}ms"


def test_dom_content_loaded(page: Page) -> None:
    dcl = _navigation_timing(page)["domContentLoaded"]
    assert dcl < DOM_CONTENT_LOADED_BUDGET_MS, (
        f"DOMContentLoaded {dcl:.0f}ms supera el presupuesto de {DOM_CONTENT_LOADED_BUDGET_MS}ms"
    )


def test_full_load(page: Page) -> None:
    load = _navigation_timing(page)["load"]
    assert load < LOAD_BUDGET_MS, f"load {load:.0f}ms supera el presupuesto de {LOAD_BUDGET_MS}ms"


def test_largest_contentful_paint(page: Page) -> None:
    lcp = _largest_contentful_paint(page)
    assert lcp > 0, "No se pudo medir el LCP"
    assert lcp < LCP_BUDGET_MS, f"LCP {lcp:.0f}ms supera el presupuesto de {LCP_BUDGET_MS}ms"


def test_cumulative_layout_shift(page: Page) -> None:
    cls = _cumulative_layout_shift(page)
    assert cls < CLS_BUDGET, f"CLS {cls:.3f} supera el presupuesto de {CLS_BUDGET}"


def test_page_weight(page: Page) -> None:
    weight_kb = _navigation_timing(page)["transferSizeKb"]
    assert weight_kb < PAGE_WEIGHT_BUDGET_KB, (
        f"La página transfiere {weight_kb:.0f}KB, supera el presupuesto de {PAGE_WEIGHT_BUDGET_KB}KB"
    )


# --- Tests de cold-start y carga de recursos (navegación propia) -------------
# Estos parten de una caché vacía para reflejar a un visitante nuevo.


def _fresh_load(browser: Browser, url: str) -> tuple[Page, list]:
    """Navega con una caché limpia y devuelve (page, responses) para inspeccionar."""
    context = browser.new_context()  # contexto nuevo = sin caché compartida
    page = context.new_page()
    responses: list = []
    page.on("response", lambda r: responses.append(r))
    page.goto(url, wait_until="load")
    page.wait_for_timeout(300)
    return page, responses


def test_no_broken_requests(browser: Browser, live_server: str) -> None:
    """Ningún recurso de la página debe devolver 4xx/5xx (atrapa rutas rotas)."""
    page, responses = _fresh_load(browser, live_server)
    broken = [(r.url, r.status) for r in responses if r.status >= 400]
    page.context.close()
    assert not broken, f"Recursos rotos: {broken}"


def test_cold_start_lcp(browser: Browser, live_server: str) -> None:
    """El LCP en frío (primer visitante, sin caché) debe entrar en presupuesto.

    Es el caso que más sufría antes de auto-hostear las fuentes: el request
    render-blocking a Google Fonts disparaba el LCP a ~2.8s.
    """
    page, _ = _fresh_load(browser, live_server)
    lcp = _largest_contentful_paint(page)
    page.context.close()
    assert 0 < lcp < LCP_BUDGET_MS, f"LCP en frío {lcp:.0f}ms supera el presupuesto de {LCP_BUDGET_MS}ms"


def test_heavy_videos_are_deferred(browser: Browser, live_server: str) -> None:
    """Los videos de fondo (varios MB) no deben descargarse en la carga inicial.

    Se cargan recién al hacer scroll a su sección (lazy), para no competir con
    los recursos críticos ni generar jank de decodificación durante el scroll.
    """
    page, responses = _fresh_load(browser, live_server)
    video_reqs = [r.url for r in responses if r.url.endswith((".webm", ".mp4"))]
    page.context.close()
    assert not video_reqs, f"Se descargaron videos en la carga inicial: {video_reqs}"


def test_no_render_blocking_third_party_fonts(browser: Browser, live_server: str) -> None:
    """Las fuentes deben servirse desde el mismo origen, no desde Google Fonts."""
    page, responses = _fresh_load(browser, live_server)
    google = [r.url for r in responses if "fonts.googleapis.com" in r.url or "fonts.gstatic.com" in r.url]
    page.context.close()
    assert not google, f"Hay requests a Google Fonts (deberían estar auto-hosteadas): {google}"
