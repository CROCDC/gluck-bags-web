from __future__ import annotations

from app.factory import db
from app.models import Product


class ProductRepository:
    """Database access for products. Routes call these, not raw db.session."""

    @staticmethod
    def get_published() -> list[Product]:
        return (
            Product.query.filter_by(is_published=True)
            .order_by(Product.position.asc(), Product.id.asc())
            .all()
        )

    @staticmethod
    def get_all() -> list[Product]:
        return Product.query.order_by(Product.position.asc(), Product.id.asc()).all()

    @staticmethod
    def get_by_id(product_id: int) -> Product | None:
        return db.session.get(Product, product_id)

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
