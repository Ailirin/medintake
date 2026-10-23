"""Командная проверка файлов и каталогов. Использует то же ядро, что и API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from medintake.batch import validate_batch
from medintake.config import Settings, configure_logging
from medintake.errors import AppError
from medintake.intake import collect_xml_paths
from medintake.reports import render_text, write_csv_report, write_json_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Приёмка медицинских XML перед передачей в страховую (ОМС/ДМС).",
    )
    parser.add_argument("inputs", nargs="+", help="XML-файлы или каталоги с XML")
    parser.add_argument(
        "--schema",
        type=Path,
        help="Путь к XSD. По умолчанию схема пакета.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "csv"),
        default="text",
        help="Формат отчёта",
    )
    parser.add_argument("--output", type=Path, help="Файл отчёта для json и csv")
    parser.add_argument(
        "--icd10-mode",
        choices=("off", "warn", "error"),
        help="Режим справочника МКБ-10. По умолчанию из конфигурации.",
    )
    return parser


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            continue


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        _configure_stdio()
    args = build_parser().parse_args(argv)
    if args.format in ("json", "csv") and args.output is None:
        print("Ошибка: для форматов json и csv укажите --output.", file=sys.stderr)
        return 2

    try:
        settings = Settings()
    except Exception as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    updates: dict = {}
    if args.schema is not None:
        updates["schema_path"] = args.schema
    if args.icd10_mode is not None:
        updates["icd10_mode"] = args.icd10_mode
    if updates:
        settings = settings.model_copy(update=updates)

    configure_logging(settings.log_level)
    try:
        paths = collect_xml_paths(args.inputs, max_files=settings.max_files)
        report = validate_batch(paths, settings)
    except (AppError, OSError, ValueError) as exc:
        message = exc.message if isinstance(exc, AppError) else str(exc)
        print(f"Ошибка: {message}", file=sys.stderr)
        return 2

    if args.format == "text":
        print(render_text(report))
    elif args.format == "json":
        write_json_report(report, args.output)
        print(f"Отчёт сохранён: {args.output}")
    else:
        write_csv_report(report, args.output)
        print(f"Отчёт сохранён: {args.output}")

    return 0 if report.invalid_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
