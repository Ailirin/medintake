"""Лимит сессии и уникальность между файлами."""

from medintake.batch import validate_batch
from medintake.errors import BatchLimitExceeded, EmptyBatch
from medintake.intake import collect_xml_paths
from tests.xml_factory import EXAMPLES, FIXED_TODAY, build_xml, write_xml


def test_duplicate_document_id_across_files(settings):
    report = validate_batch(
        [EXAMPLES / "duplicate_a.xml", EXAMPLES / "duplicate_b.xml"],
        settings,
        today=FIXED_TODAY,
    )
    assert report.invalid_count == 2
    assert all(
        any(issue.code == "DUPLICATE_DOCUMENT_ID" for issue in result.issues)
        for result in report.results
    )


def test_duplicate_content(settings, tmp_path):
    payload = build_xml()
    first = write_xml(tmp_path, "a.xml", content=payload)
    second = write_xml(tmp_path, "b.xml", content=payload)
    report = validate_batch([first, second], settings, today=FIXED_TODAY)
    assert all(
        any(issue.code == "DUPLICATE_CONTENT" for issue in result.issues)
        for result in report.results
    )


def test_batch_limit_is_checked_before_reading(settings, tmp_path):
    limited = settings.model_copy(update={"max_files": 1})
    files = [
        write_xml(tmp_path, "a.xml", content=build_xml(document_id="A")),
        write_xml(tmp_path, "b.xml", content=build_xml(document_id="B")),
    ]
    try:
        validate_batch(files, limited, today=FIXED_TODAY)
    except BatchLimitExceeded as exc:
        assert exc.limit == 1
        assert "не больше 1" in exc.message
    else:
        raise AssertionError("лимит пакета не сработал")


def test_empty_batch(settings):
    try:
        validate_batch([], settings, today=FIXED_TODAY)
    except EmptyBatch:
        return
    raise AssertionError("пустой пакет принят")


def test_file_too_large_does_not_stop_the_batch(settings, tmp_path):
    ok = write_xml(tmp_path, "ok.xml", content=build_xml(document_id="DOC-OK"))
    huge = write_xml(
        tmp_path,
        "huge.xml",
        content=build_xml(
            document_id="DOC-HUGE",
            diagnoses=[("J06.9", "А" * 4000, True)],
        ),
    )
    limited = settings.model_copy(update={"max_file_bytes": ok.stat().st_size})
    report = validate_batch([huge, ok], limited, today=FIXED_TODAY)
    assert [issue.code for issue in report.results[0].issues] == ["FILE_TOO_LARGE"]
    assert report.results[1].issues == []


def test_collect_deduplicates_and_reads_directory(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    write_xml(folder, "b.xml", content=build_xml(document_id="B"))
    write_xml(folder, "a.xml", content=build_xml(document_id="A"))
    (folder / "note.txt").write_text("не xml", encoding="utf-8")
    nested = folder / "nested"
    nested.mkdir()
    write_xml(nested, "hidden.xml", content=build_xml(document_id="H"))

    first = folder / "a.xml"
    paths = collect_xml_paths([str(folder), str(first)], max_files=10)
    assert [path.name for path in paths] == ["a.xml", "b.xml"]


def test_collect_missing_path(tmp_path):
    try:
        collect_xml_paths([str(tmp_path / "missing.xml")], max_files=10)
    except FileNotFoundError:
        return
    raise AssertionError("битый путь не отклонён")
