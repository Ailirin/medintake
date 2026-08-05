"""Точка входа CLI: запуск валидации из командной строки."""

import argparse
import csv
import json
import sys
from pathlib import Path

from medxml_validator.validator import ValidationResult, validate_files


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Валидация медицинских XML-документов",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="XML-файлы или папки с XML",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/medical_document.xsd"),
        help="Путь к XSD-схеме",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "csv"),
        default="text",
        help="Формат отчёта: text (консоль), json или csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Файл отчёта (обязателен для json и csv)",
    )
    return parser


def collect_xml_paths(inputs: list[str]) -> list[Path]:
    """Собирает список XML-файлов из путей к файлам и папкам."""
    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.xml")))
        elif path.is_file():
            paths.append(path)
        else:
            raise FileNotFoundError(f"Путь не найден: {path}")

    if not paths:
        raise ValueError("Не найдено ни одного XML-файла")

    return paths


def print_report(results: list[ValidationResult]) -> int:
    """Печатает отчёт в консоль. Возвращает код выхода: 0 — всё ок, 1 — есть ошибки."""
    exit_code = 0

    for result in results:
        status = "OK" if result.is_valid else "FAILED"
        print(f"\n[{status}] {result.file_path}")

        for issue in result.issues:
            location = issue.xpath or f"строка {issue.line}"
            print(f"  [{issue.severity.value}] {issue.code}: {issue.message} ({location})")
            if issue.severity.value == "ERROR":
                exit_code = 1

    valid = sum(1 for result in results if result.is_valid)
    print(f"\nИтого: {valid}/{len(results)} файлов прошли проверку")
    return exit_code


def write_json_report(results: list[ValidationResult], output_path: Path) -> None:
    """Сохраняет отчёт в JSON."""
    payload = {
        "summary": {
            "total": len(results),
            "valid": sum(1 for result in results if result.is_valid),
            "errors": sum(result.error_count for result in results),
            "warnings": sum(result.warning_count for result in results),
        },
        "results": [
            {
                "file": str(result.file_path),
                "document_id": result.document_id,
                "valid": result.is_valid,
                "issues": [issue.to_dict() for issue in result.issues],
            }
            for result in results
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv_report(results: list[ValidationResult], output_path: Path) -> None:
    """Сохраняет отчёт в CSV."""
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "file",
                "document_id",
                "severity",
                "code",
                "message",
                "line",
                "column",
                "xpath",
            ],
        )
        writer.writeheader()

        for result in results:
            if not result.issues:
                writer.writerow(
                    {
                        "file": str(result.file_path),
                        "document_id": result.document_id or "",
                        "severity": "OK",
                        "code": "",
                        "message": "",
                        "line": "",
                        "column": "",
                        "xpath": "",
                    }
                )
                continue

            for issue in result.issues:
                writer.writerow(
                    {
                        "file": str(result.file_path),
                        "document_id": result.document_id or "",
                        "severity": issue.severity.value,
                        "code": issue.code,
                        "message": issue.message,
                        "line": issue.line or "",
                        "column": issue.column or "",
                        "xpath": issue.xpath or "",
                    }
                )


def main() -> int:
    """Главная функция CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.schema.is_file():
        print(f"Ошибка: XSD-схема не найдена: {args.schema}", file=sys.stderr)
        return 2

    if args.format in ("json", "csv") and args.output is None:
        print("Ошибка: для форматов json и csv укажите --output.", file=sys.stderr)
        return 2

    try:
        xml_paths = collect_xml_paths(args.inputs)
        results = validate_files(xml_paths, args.schema)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    if args.format == "text":
        return print_report(results)

    if args.format == "json":
        write_json_report(results, args.output)
    else:
        write_csv_report(results, args.output)

    print(f"Отчёт сохранён: {args.output}")
    return 0 if all(result.is_valid for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
