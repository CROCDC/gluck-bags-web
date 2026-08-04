"""Data-access layer, re-exported for `from app.repositories import ProductRepository`."""

from app.repositories.product_repository import ProductRepository
from app.repositories.site_text_repository import SiteTextRepository

__all__ = ["ProductRepository", "SiteTextRepository"]
