"""REST API приёма пакетов и выдачи отчётов."""

from __future__ import annotations

import logging
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from medintake import __version__
from medintake.checks import ALL_CHECKS
from medintake.config import Settings, configure_logging
from medintake.errors import AppError
from medintake.intake import resolve_inbox_directory, stage_directory, stage_uploads
from medintake.jobs import BatchWorker, JobStore

logger = logging.getLogger("medintake.api")
_bearer = HTTPBearer(auto_error=False, description="Токен MEDINTAKE_API_TOKEN, если он задан.")

# Форма разбирается вручную: у Starlette по умолчанию предел 1000 файлов, а сессия — до 10 000.
_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["files"],
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "XML-файлы одной сессии",
                        }
                    },
                }
            }
        },
    }
}
_UPLOAD_FIELD = "files"
_MAX_FORM_FIELDS = 16


class DirectoryIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(
        min_length=1,
        max_length=240,
        examples=["2026-10-22/smo-77001"],
        description="Путь относительно каталога приёма MEDINTAKE_INBOX_ROOT.",
    )


class AuthError(Exception):
    """Запрос к API без верного токена."""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = JobStore(settings.database_path)
        worker = BatchWorker(store, settings)
        app.state.store = store
        app.state.worker = worker
        worker.start()
        try:
            yield
        finally:
            worker.stop()
            store.close()

    app = FastAPI(
        title="Приёмка медицинских документов",
        version=__version__,
        description=(
            "Внутренний контур предварительной проверки документов, "
            "которые передаются в страховые организации по ОМС и ДМС."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(_router())

    @app.get("/health", tags=["service"])
    def health(request: Request) -> JSONResponse:
        worker_ok = request.app.state.worker.is_alive
        database_ok = request.app.state.store.ping()
        healthy = worker_ok and database_ok
        return JSONResponse(
            {
                "status": "ok" if healthy else "degraded",
                "version": __version__,
                "worker": "running" if worker_ok else "stopped",
                "database": "ok" if database_ok else "unavailable",
            },
            status_code=200 if healthy else 503,
        )

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": str(exc.detail)},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(AuthError)
    async def handle_auth(_request: Request, _exc: AuthError) -> JSONResponse:
        return JSONResponse({"error": "Требуется авторизация."}, status_code=401)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": "Некорректный запрос.", "details": jsonable_encoder(exc.errors())},
            status_code=422,
        )

    return app


def _router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(_require_token)])

    @router.get("/checks", tags=["checks"])
    def list_checks() -> dict:
        return {"checks": [item.to_dict() for item in ALL_CHECKS]}

    @router.get("/batches", tags=["batches"])
    def list_batches(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        jobs = _store(request).list_recent(limit)
        return {"batches": [job.to_dict() for job in jobs]}

    @router.post(
        "/batches",
        status_code=202,
        tags=["batches"],
        openapi_extra=_UPLOAD_OPENAPI,
    )
    async def submit_uploads(request: Request) -> dict:
        settings = _settings(request)
        try:
            form = await request.form(
                max_files=settings.max_files,
                max_fields=_MAX_FORM_FIELDS,
            )
        except StarletteHTTPException as exc:
            raise AppError(
                f"Загрузка не принята ({exc.detail}). "
                f"За одну сессию принимается не больше {settings.max_files} файлов."
            ) from exc
        try:
            uploads = [item for item in form.getlist(_UPLOAD_FIELD) if isinstance(item, UploadFile)]
            return await run_in_threadpool(_accept_uploads, request, uploads)
        finally:
            await form.close()

    @router.post("/batches/directory", status_code=202, tags=["batches"])
    def submit_directory(request: Request, body: DirectoryIntake) -> dict:
        settings = _settings(request)
        source = resolve_inbox_directory(settings.inbox, body.directory)
        job_id = uuid4().hex
        batch_dir = settings.batches_dir / job_id
        input_dir = batch_dir / "input"
        try:
            items = stage_directory(
                source,
                input_dir,
                max_files=settings.max_files,
                max_file_bytes=settings.max_file_bytes,
            )
            job = _store(request).enqueue(
                job_id=job_id,
                source="directory",
                total=len(items),
                input_dir=input_dir,
            )
        except Exception:
            shutil.rmtree(batch_dir, ignore_errors=True)
            raise
        return job.to_dict()

    @router.get("/batches/{batch_id}", tags=["batches"])
    def batch_status(request: Request, batch_id: str) -> JSONResponse:
        job = _find_job(request, batch_id)
        if job is None:
            return _unknown_batch()
        return JSONResponse(job.to_dict())

    @router.get("/batches/{batch_id}/report", tags=["batches"])
    def batch_report(
        request: Request,
        batch_id: str,
        report_format: Literal["json", "csv"] = Query(default="json", alias="format"),
    ) -> Response:
        job = _find_job(request, batch_id)
        if job is None:
            return _unknown_batch()
        if job.status != "completed" or not job.report_path:
            return JSONResponse(
                {
                    "error": "Отчёт ещё не готов.",
                    "status": job.status,
                    "message": job.error,
                },
                status_code=409,
            )
        path = Path(job.report_path)
        if report_format == "csv":
            path = path.with_suffix(".csv")
            media_type = "text/csv; charset=utf-8"
            filename = f"batch-{batch_id}.csv"
        else:
            media_type = "application/json"
            filename = f"batch-{batch_id}.json"
        if not path.is_file():
            return JSONResponse({"error": "Файл отчёта не найден."}, status_code=404)
        return FileResponse(path, media_type=media_type, filename=filename)

    return router


def _accept_uploads(request: Request, uploads: list[UploadFile]) -> dict:
    settings = _settings(request)
    job_id = uuid4().hex
    batch_dir = settings.batches_dir / job_id
    input_dir = batch_dir / "input"
    try:
        items = stage_uploads(
            ((upload.filename or "document.xml", upload.file) for upload in uploads),
            input_dir,
            max_files=settings.max_files,
            max_file_bytes=settings.max_file_bytes,
        )
        job = _store(request).enqueue(
            job_id=job_id,
            source="upload",
            total=len(items),
            input_dir=input_dir,
        )
    except Exception:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise
    return job.to_dict()


def _require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    token = _settings(request).api_token
    if not token:
        return
    provided = credentials.credentials if credentials is not None else ""
    if len(provided) != len(token) or not secrets.compare_digest(provided, token):
        raise AuthError()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _find_job(request: Request, batch_id: str):
    if len(batch_id) != 32 or any(character not in "0123456789abcdef" for character in batch_id):
        return None
    return _store(request).get(batch_id)


def _unknown_batch() -> JSONResponse:
    return JSONResponse({"error": "Пакет не найден."}, status_code=404)


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    logger.info("Сервис проверки XML слушает %s:%s", settings.host, settings.port)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
