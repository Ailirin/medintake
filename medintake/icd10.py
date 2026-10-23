"""Загрузка справочника МКБ-10.

Файл ведёт аналитик: code,title в UTF-8 (с BOM тоже принимается — так сохраняет Excel).
Это рабочий экстракт, а не полный классификатор НСИ. Полнота включается режимом error.
"""

from __future__ import annotations

import csv
from pathlib import Path

from medintake.errors import CatalogError


def load_icd10(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise CatalogError(f"Справочник МКБ-10 не найден: {path}")

    codes: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not {"code", "title"}.issubset(set(reader.fieldnames)):
                raise CatalogError(
                    "В справочнике МКБ-10 нужны колонки code и title."
                )
            for line_no, row in enumerate(reader, start=2):
                code = (row.get("code") or "").strip().upper()
                title = (row.get("title") or "").strip()
                if not code:
                    continue
                if code in codes:
                    raise CatalogError(
                        f"Код МКБ-10 «{code}» повторяется в справочнике, строка {line_no}."
                    )
                codes[code] = title
    except CatalogError:
        raise
    except OSError as exc:
        raise CatalogError(f"Не удалось прочитать справочник МКБ-10: {exc.strerror or exc}") from exc
    except csv.Error as exc:
        raise CatalogError(f"Справочник МКБ-10 повреждён: {exc}") from exc

    if not codes:
        raise CatalogError(f"Справочник МКБ-10 пуст: {path}")
    return codes
