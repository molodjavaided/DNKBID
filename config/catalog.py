"""
Product catalog — single source of truth for categories and items.
Mirrors src/config/catalog.ts.
"""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CatalogItem:
    key: str
    name: str


@dataclass(frozen=True)
class CatalogCategory:
    key: str
    name: str
    items: List[CatalogItem]


CATALOG: List[CatalogCategory] = [
    CatalogCategory(key="milk", name="🥛 Молоко", items=[
        CatalogItem(key="oat",     name="Овсяное молоко"),
        CatalogItem(key="banana",  name="Банановое молоко"),
        CatalogItem(key="almond",  name="Миндальное молоко"),
        CatalogItem(key="coconut", name="Кокосовое молоко"),
        CatalogItem(key="reg32",   name="Молоко 3.2%"),
    ]),
    CatalogCategory(key="coffee", name="☕ Кофе", items=[
        CatalogItem(key="espresso", name="Эспрессо бленд"),
        CatalogItem(key="filter",   name="Кофе для фильтра"),
        CatalogItem(key="decaf",    name="Без кофеина"),
        CatalogItem(key="cold",     name="Кофе для колд-брю"),
    ]),
    CatalogCategory(key="tea", name="🍵 Чай", items=[
        CatalogItem(key="english", name="Английский завтрак"),
        CatalogItem(key="green",   name="Зелёный чай"),
        CatalogItem(key="fruit",   name="Фруктовый чай"),
        CatalogItem(key="mint",    name="Мятный чай"),
    ]),
    CatalogCategory(key="syrup", name="🧴 Сиропы", items=[
        CatalogItem(key="vanilla",   name="Ванильный"),
        CatalogItem(key="caramel",   name="Карамельный"),
        CatalogItem(key="lavender",  name="Лавандовый"),
        CatalogItem(key="pistachio", name="Фисташковый"),
        CatalogItem(key="hazelnut",  name="Ореховый"),
    ]),
    CatalogCategory(key="supply", name="📦 Расходники", items=[
        CatalogItem(key="cup250", name="Стаканы 250мл"),
        CatalogItem(key="cup400", name="Стаканы 400мл"),
        CatalogItem(key="lid",    name="Крышки"),
        CatalogItem(key="straw",  name="Трубочки"),
        CatalogItem(key="sleeve", name="Манжеты"),
        CatalogItem(key="napkin", name="Салфетки"),
    ]),
    CatalogCategory(key="other", name="🛒 Другое", items=[
        CatalogItem(key="sugar",    name="Сахар"),
        CatalogItem(key="cinnamon", name="Корица"),
        CatalogItem(key="cocoa",    name="Какао-порошок"),
        CatalogItem(key="honey",    name="Мёд"),
    ]),
]

# Standard quantity options shown as buttons
QTY_OPTIONS: List[int] = [1, 2, 5, 10]
