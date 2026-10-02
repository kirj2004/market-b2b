"""Каталог услуг для кабинета.

Цены, сроки и названия берутся из канона `operations/registry.py` — это
единственный источник правды. Кабинет ничего не дублирует руками: если
в реестре поменялась цена, здесь она меняется автоматически.

Если реестр недоступен (каталог операционки не смонтирован), приложение
поднимается с пустым каталогом и пишет предупреждение — заявку создать
нельзя, но логины и админка работают.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from config import OPS_DIR

log = logging.getLogger(__name__)

Level = Literal["micro", "main", "packs"]

LEVEL_LABELS: dict[str, str] = {
    "micro": "Микропродукты",
    "main": "Основные продукты",
    "packs": "Пакеты и абонементы",
}


@dataclass(frozen=True, slots=True)
class Service:
    """Паспорт услуги в том виде, в каком его видит клиент."""

    id: str            # s01 … s19
    n: int
    name: str
    level: Level
    price: str         # «9 900», «от 29 900» — строка как в каталоге
    term: str
    pain: str
    gets: tuple[str, ...]
    inputs: tuple[str, ...]

    @property
    def price_label(self) -> str:
        return f"{self.price} ₽"

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS.get(self.level, self.level)


def _load_canon() -> object | None:
    """Загрузить operations/registry.py по пути файла, не по имени модуля."""
    path = OPS_DIR / "registry.py"
    if not path.exists():
        log.warning("Каталог услуг не найден: %s", path)
        return None
    spec = importlib.util.spec_from_file_location("ops_canon_registry", path)
    if spec is None or spec.loader is None:
        log.warning("Не удалось загрузить %s", path)
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ops_canon_registry"] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def all_services() -> tuple[Service, ...]:
    """Каталог услуг, отсортированный по номеру."""
    canon = _load_canon()
    if canon is None:
        return ()

    result: list[Service] = []
    for svc in canon.SERVICES:  # type: ignore[attr-defined]
        result.append(
            Service(
                id=f"s{svc.n:02d}",
                n=svc.n,
                name=svc.name,
                level=svc.level,
                price=svc.price,
                term=svc.term,
                pain=svc.pain,
                gets=tuple(svc.gets),
                inputs=tuple(svc.inputs),
            )
        )
    result.sort(key=lambda s: s.n)
    return tuple(result)


@lru_cache(maxsize=1)
def services_by_level() -> tuple[tuple[Level, tuple[Service, ...]], ...]:
    """Каталог, сгруппированный по уровню, в порядке micro → main → packs."""
    order: tuple[Level, ...] = ("micro", "main", "packs")
    grouped = [(lvl, tuple(s for s in all_services() if s.level == lvl)) for lvl in order]
    return tuple((lvl, items) for lvl, items in grouped if items)


def get_service(service_id: str) -> Service | None:
    """Услуга по идентификатору `sNN`. None, если такой услуги нет."""
    if not service_id:
        return None
    return next((s for s in all_services() if s.id == service_id), None)


def services_by_ids(ids: list[str] | tuple[str, ...]) -> tuple[Service, ...]:
    """Несколько услуг по списку идентификаторов, в порядке каталога."""
    wanted = set(ids)
    return tuple(s for s in all_services() if s.id in wanted)
