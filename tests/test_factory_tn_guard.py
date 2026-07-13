"""Regression test for the boot guard around the Tienda Nube wiring.

A missing dependency in the TN wiring once crashed create_app and 502'd the whole
site. The factory now isolates that wiring so the core storefront boots regardless.
This pins that behaviour: if `register_tiendanube` blows up, the app still builds and
serves the storefront/cart, only the TN endpoints are absent.
"""

from __future__ import annotations


def test_tn_wiring_failure_does_not_crash_boot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEED_PRODUCTS", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", "x")
    monkeypatch.setenv("SECRET_KEY", "k")

    import app.tiendanube as tn_mod

    def boom(_app) -> None:
        raise RuntimeError("simulated Tienda Nube wiring failure")

    monkeypatch.setattr(tn_mod, "register_tiendanube", boom)

    from app.factory import create_app

    application = create_app()  # must NOT raise despite the TN wiring failing

    rules = {str(r) for r in application.url_map.iter_rules()}
    # Core storefront + cart still up...
    assert "/" in rules
    assert "/carrito" in rules
    # ...but the TN endpoints were skipped (wiring failed, guard swallowed it).
    assert "/webhooks/tiendanube" not in rules
