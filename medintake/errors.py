"""Ошибки, которые можно показать оператору без трассировки."""


class AppError(Exception):
    """Ожидаемая ошибка приёма пакета или конфигурации."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BatchLimitExceeded(AppError):
    def __init__(self, actual: int, limit: int) -> None:
        super().__init__(
            f"В пакете {actual} файлов. За одну сессию принимается не больше {limit}."
        )
        self.actual = actual
        self.limit = limit


class EmptyBatch(AppError):
    def __init__(self) -> None:
        super().__init__("В пакете нет XML-файлов.")


class InboxPathError(AppError):
    """Каталог приёма указан вне разрешённого корня или не существует."""


class CatalogError(AppError):
    """Справочник МКБ-10 не читается или собран с ошибкой."""
