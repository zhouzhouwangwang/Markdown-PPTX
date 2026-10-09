"""FastAPI surface for the standalone Markdown to PPTX service.

Endpoints:
    GET  /healthz                      dependency and configuration probe
    POST /v1/decks                     render a manuscript into a PPTX
    GET  /v1/decks/{task_id}           previous result document
    GET  /v1/decks/{task_id}/pptx      download the generated deck
    POST /v1/assets                    upload an image for markdown references
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .pipeline import (
    DeckRequest,
    RenderError,
    ValidationError,
    VerificationError,
    generate,
    load_result,
    preview,
)
from .render import CANVAS, DEFAULT_MAX_BULLETS
from .settings import Settings, get_settings

app = FastAPI(
    title="PPT Service",
    version="1.0.0",
    summary="Deterministic Markdown to editable PPTX conversion (no LLM).",
)


class DeckIn(BaseModel):
    """A structured manuscript and the options for rendering it."""

    markdown: str = Field(
        ...,
        description="Structured manuscript: '# title', '## section', '- bullet', '> lead'.",
        min_length=1,
    )
    aspect_ratio: str = Field("16:9", description="One of 16:9, 4:3, A1.")
    theme: str = Field(
        "classic", description="Visual theme: classic, swiss or midnight."
    )
    max_bullets: int = Field(
        DEFAULT_MAX_BULLETS,
        ge=1,
        le=12,
        description="Upper bound of list items per page; pagination is "
        "height-aware, so long (multi-line) bullets split earlier.",
    )
    name: str | None = Field(None, description="Deck name used for PPTX metadata.")
    title: str | None = Field(None, description="Explicit PPTX title metadata.")
    author: str = Field("pptsvc", description="PPTX author metadata.")
    dry_run: bool = Field(
        False, description="Validate the slides and skip PPTX generation."
    )

    def to_request(self) -> DeckRequest:
        if self.aspect_ratio not in CANVAS:
            raise RenderError(
                f"unsupported aspect_ratio {self.aspect_ratio!r}; "
                f"expected one of {', '.join(CANVAS)}"
            )
        return DeckRequest(
            markdown=self.markdown,
            aspect_ratio=self.aspect_ratio,
            theme=self.theme,
            max_bullets=self.max_bullets,
            name=self.name,
            dry_run=self.dry_run,
            author=self.author,
            title=self.title,
        )


def _settings() -> Settings:
    return get_settings()


async def _node_version(node_bin: str) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            node_bin,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (OSError, asyncio.TimeoutError):
        return None
    return stdout.decode("utf-8", errors="replace").strip() or None


@app.get("/", include_in_schema=False, summary="Browser test form for the API")
async def index() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "index.html",
        media_type="text/html",
    )


@app.get("/healthz", summary="Probe runtime dependencies")
async def healthz() -> dict[str, Any]:
    settings = _settings()
    node = await _node_version(settings.node_bin)
    output_dir = settings.output_dir
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        writable = True
    except OSError:
        writable = False
    themes = (
        sorted(
            entry.name
            for entry in settings.templates_dir.iterdir()
            if (entry / "section.css").is_file()
        )
        if settings.templates_dir.is_dir()
        else []
    )
    checks = {
        "node_available": node is not None,
        "converter_present": settings.converter.is_file(),
        "templates_present": bool(themes),
        "output_writable": writable,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "node": node,
        "converter": str(settings.converter),
        "templates_dir": str(settings.templates_dir),
        "themes": themes,
        "output_dir": str(output_dir),
        "node_path": settings.node_path,
        "browsers_path": settings.browsers_path,
        "max_concurrency": settings.max_concurrency,
        "timeout_seconds": settings.timeout_seconds,
    }


@app.post("/v1/decks", summary="Render a manuscript into a PPTX")
async def create_deck(payload: DeckIn) -> JSONResponse:
    settings = _settings()
    try:
        result = await generate(payload.to_request(), settings)
    except RenderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "slide validation failed", "errors": exc.errors},
        ) from exc
    except VerificationError as exc:
        raise HTTPException(
            status_code=500,
            detail={"message": "generated PPTX failed verification", "checks": exc.checks},
        ) from exc
    if result["artifact"] is not None:
        result["download_url"] = f"/v1/decks/{result['task_id']}/pptx"
    return JSONResponse(result)


@app.post("/v1/preview", summary="Render pages to HTML for visual preview")
async def preview_deck(payload: DeckIn) -> dict[str, Any]:
    """Preview the manuscript in a theme: real HTML, no conversion."""
    try:
        return preview(payload.to_request(), _settings())
    except RenderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ASSET_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]")
_ASSET_MAX_BYTES = 10 * 1024 * 1024


@app.post("/v1/assets", status_code=201, summary="Upload an image asset")
async def upload_asset(file: UploadFile = File(...)) -> dict[str, Any]:
    """Store an image under assets/ and return its markdown reference path.

    The filename is sanitised; a collision overwrites deterministically.
    """
    raw_name = Path(file.filename or "image").name
    ext = Path(raw_name).suffix.lower()
    if ext not in _ASSET_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported image type {ext!r}; allowed: {sorted(_ASSET_EXTS)}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > _ASSET_MAX_BYTES:
        raise HTTPException(status_code=400, detail="image exceeds the 10MB limit")
    safe = _ASSET_NAME_RE.sub("_", raw_name)[:120]
    assets_dir = _settings().assets_dir
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / safe).write_bytes(data)
    return {"filename": safe, "path": f"assets/{safe}", "size": len(data)}


@app.get("/v1/decks/{task_id}", summary="Read a previous result document")
async def read_deck(task_id: str) -> dict[str, Any]:
    result = load_result(_settings(), task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="unknown task_id")
    if result.get("artifact"):
        result["download_url"] = f"/v1/decks/{task_id}/pptx"
    return result


@app.get("/v1/decks/{task_id}/pptx", summary="Download a generated deck")
async def download_deck(task_id: str) -> FileResponse:
    settings = _settings()
    result = load_result(settings, task_id)
    if result is None or not result.get("artifact"):
        raise HTTPException(status_code=404, detail="unknown task_id")
    path = (settings.output_dir / result["artifact"]).resolve()
    if not path.is_file() or not path.is_relative_to(settings.output_dir.resolve()):
        raise HTTPException(status_code=404, detail="artifact missing")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=Path(result["artifact"]).name,
    )
