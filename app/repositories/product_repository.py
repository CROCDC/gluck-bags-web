from __future__ import annotations

from sqlalchemy.orm import selectinload

from app.factory import db
from app.models import Product


class ProductRepository:
    """Database access for products. Routes call these, not raw db.session."""

    @staticmethod
    def get_published() -> list[Product]:
        # selectinload the media so rendering the grid is 2 queries, not 1+N.
        return (
            Product.query.options(selectinload(Product.media))
            .filter_by(is_published=True)
            .order_by(Product.position.asc(), Product.id.asc())
            .all()
        )

    @staticmethod
    def get_all() -> list[Product]:
        return (
            Product.query.options(selectinload(Product.media))
            .order_by(Product.position.asc(), Product.id.asc())
            .all()
        )

    @staticmethod
    def get_by_id(product_id: int) -> Product | None:
        return db.session.get(Product, product_id)

    @staticmethod
    def get_published_by_category(category: str) -> list[Product]:
        return (
            Product.query.options(selectinload(Product.media))
            .filter_by(is_published=True, category=category)
            .order_by(Product.position.asc(), Product.id.asc())
            .all()
        )

    @staticmethod
    def published_categories() -> list[str]:
        """Distinct, non-empty categories among published products, in grid order."""
        seen: list[str] = []
        for product in ProductRepository.get_published():
            if product.category and product.category not in seen:
                seen.append(product.category)
        return seen

    @staticmethod
    def get_related(product: Product, limit: int = 4) -> list[Product]:
        """Other published products, same category first, excluding `product`."""
        others = [p for p in ProductRepository.get_published() if p.id != product.id]
        same = [p for p in others if product.category and p.category == product.category]
        rest = [p for p in others if p not in same]
        return (same + rest)[:limit]

    @staticmethod
    def next_position() -> int:
        last = Product.query.order_by(Product.position.desc()).first()
        return (last.position + 1) if last else 0

    @staticmethod
    def create(
        title: str,
        description: str = "",
        price: int | None = None,
        category: str | None = None,
        is_published: bool = True,
    ) -> Product:
        product = Product(
            title=title,
            description=description or "",
            price=price,
            category=category or None,
            is_published=is_published,
            position=ProductRepository.next_position(),
        )
        db.session.add(product)
        db.session.commit()
        return product

    @staticmethod
    def save() -> None:
        db.session.commit()

    @staticmethod
    def delete(product: Product) -> None:
        db.session.delete(product)
        db.session.commit()
