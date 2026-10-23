"""Приём файлов в сессию: каталог оператора, каталог inbox и загрузка по API."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from medintake.errors import BatchLimitExceeded, EmptyBatch, InboxPathError
from medintake.models import BatchItem

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_MANIFEST = "manifest.json"


def collect_xml_paths(inputs: Iterable[str], *, max_files: int) -> list[Path]:
    """Собирает XML из явно переданных файлов и из каталогов (без вложенных папок)."""
    seen: set[Path] = set()
    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            candidates = _xml_files(path)
        elif path.is_file():
            candidates = [path]
        else:
            raise FileNotFoundError(f"Путь не найден: {path}")
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(candidate)

    if not paths:
        raise EmptyBatch()
    if len(paths) > max_files:
        raise BatchLimitExceeded(len(paths), max_files)
    return paths


def resolve_inbox_directory(inbox_root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or any(part == ".." for part in raw.parts):
        raise InboxPathError(
            "Каталог указывается относительно папки приёма, без выхода наверх."
        )
    root = inbox_root.resolve()
    target = (root / raw).resolve()
    if not target.is_relative_to(root):
        raise InboxPathError("Каталог выходит за пределы папки приёма.")
    if not target.is_dir():
        raise InboxPathError(f"Каталог не найден в папке приёма: {relative}")
    return target


def stage_directory(
    source: Path,
    batch_input: Path,
    *,
    max_files: int,
    max_file_bytes: int,
) -> list[BatchItem]:
    files = _xml_files(source)
    if not files:
        raise EmptyBatch()
    if len(files) > max_files:
        raise BatchLimitExceeded(len(files), max_files)
    return stage_paths(files, batch_input, max_file_bytes=max_file_bytes)


def stage_paths(paths: Iterable[Path], batch_input: Path, *, max_file_bytes: int) -> list[BatchItem]:
    """Копирует файлы в каталог сессии и сохраняет исходные имена в манифесте."""
    return _stage(
        ((path, path.name, path.stat().st_size) for path in paths),
        batch_input,
        max_file_bytes=max_file_bytes,
    )


def stage_uploads(
    uploads: Iterable[tuple[str, BinaryIO]],
    batch_input: Path,
    *,
    max_files: int,
    max_file_bytes: int,
) -> list[BatchItem]:
    prepared = list(uploads)
    if not prepared:
        raise EmptyBatch()
    if len(prepared) > max_files:
        raise BatchLimitExceeded(len(prepared), max_files)

    batch_input.mkdir(parents=True, exist_ok=True)
    items: list[BatchItem] = []
    manifest: list[dict[str, object]] = []
    for index, (original_name, stream) in enumerate(prepared, start=1):
        display_name = Path(original_name).name or f"document-{index}.xml"
        if not display_name.lower().endswith(".xml"):
            raise InboxPathError(f"Ожидается файл .xml, получен «{display_name}».")
        stored_name = f"{index:05d}_{_safe_stem(display_name)}.xml"
        destination = batch_input / stored_name
        too_large = _copy_limited(stream, destination, max_file_bytes)
        if too_large and destination.exists():
            destination.unlink()
        items.append(
            BatchItem(
                path=None if too_large else destination,
                display_name=display_name,
                too_large=too_large,
            )
        )
        manifest.append(
            {
                "file": None if too_large else stored_name,
                "name": display_name,
                "too_large": too_large,
            }
        )
    _write_manifest(batch_input, manifest)
    return items


def load_manifest(batch_input: Path) -> list[BatchItem]:
    manifest_path = batch_input / _MANIFEST
    if not manifest_path.is_file():
        return [
            BatchItem(path=path, display_name=path.name)
            for path in _xml_files(batch_input)
        ]

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = batch_input.resolve()
    items: list[BatchItem] = []
    for row in payload:
        stored = row.get("file")
        path: Path | None = None
        if stored:
            candidate = (batch_input / str(stored)).resolve()
            if not candidate.is_relative_to(root):
                raise InboxPathError("Манифест пакета содержит путь вне каталога сессии.")
            path = candidate
        items.append(
            BatchItem(
                path=path,
                display_name=str(row.get("name") or (path.name if path else "document.xml")),
                too_large=bool(row.get("too_large")),
            )
        )
    return items


def _stage(
    sources: Iterable[tuple[Path, str, int]],
    batch_input: Path,
    *,
    max_file_bytes: int,
) -> list[BatchItem]:
    rows = list(sources)
    if not rows:
        raise EmptyBatch()
    batch_input.mkdir(parents=True, exist_ok=True)
    items: list[BatchItem] = []
    manifest: list[dict[str, object]] = []
    for index, (source, display_name, size) in enumerate(rows, start=1):
        too_large = size > max_file_bytes
        stored_name = f"{index:05d}_{_safe_stem(display_name)}.xml"
        destination = batch_input / stored_name
        if not too_large:
            destination.write_bytes(source.read_bytes())
        items.append(
            BatchItem(
                path=None if too_large else destination,
                display_name=display_name,
                too_large=too_large,
            )
        )
        manifest.append(
            {
                "file": None if too_large else stored_name,
                "name": display_name,
                "too_large": too_large,
            }
        )
    _write_manifest(batch_input, manifest)
    return items


def _xml_files(directory: Path) -> list[Path]:
    root = directory.resolve()
    found: list[Path] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise FileNotFoundError(
            f"Не удалось прочитать каталог {directory}: {exc.strerror or exc}"
        ) from exc
    for path in entries:
        if not path.is_file() or path.suffix.lower() != ".xml":
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise InboxPathError(f"Файл «{path.name}» указывает за пределы каталога.")
        found.append(path)
    return found


def _copy_limited(stream: BinaryIO, destination: Path, max_file_bytes: int) -> bool:
    size = 0
    too_large = False
    with destination.open("wb") as handle:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_file_bytes:
                too_large = True
                break
            handle.write(chunk)
    return too_large


def _safe_stem(filename: str) -> str:
    stem = Path(filename).name
    if stem.lower().endswith(".xml"):
        stem = stem[:-4]
    cleaned = _UNSAFE_NAME.sub("_", stem).strip("._")
    return (cleaned or "document")[:80]


def _write_manifest(batch_input: Path, rows: list[dict[str, object]]) -> None:
    (batch_input / _MANIFEST).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
