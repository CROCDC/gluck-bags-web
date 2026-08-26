"""Data-access layer, re-exported for `from app.repositories import ProductRepository`."""

from app.repositories.product_repository import ProductRepository

__all__ = ["ProductRepository"]
