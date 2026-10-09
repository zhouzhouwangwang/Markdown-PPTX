"""Deck generation pipeline: markdown -> HTML slides -> PPTX -> verification.

Every step is deterministic and local: no model calls, no network access. The
converter is the vendored html2pptx CLI, invoked as a subprocess so that a
conversion failure never takes the service process down with it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .render import (
    CANVAS,
    DEFAULT_MAX_BULLETS,
    THEMES,
    render_deck,
    require_title,
)
from .settings import Settings
from .verify import verify_pptx

NUMBERED_ERROR_RE = re.compile(r"^\s*\d+\.\s*(.+)$")
FILE_PREFIX_RE = re.compile(r"^(?:Error:\s*)?(?P<path>.*?\.html):\s*(?P<rest>.*)$", re.DOTALL)
OVERFLOW_RE = re.compile(r"bottom edge|overflow", re.IGNORECASE)
# Height budgets tried in order: if real font metrics still overflow, shrink
# and re-render instead of failing the request. Deterministic sequence.
RETRY_SCALES = (1.0, 0.85, 0.72)


def _clean(message: str) -> str:
    """Keep the failing slide name, drop absolute paths from converter output."""
    stripped = message.strip()
    match = FILE_PREFIX_RE.match(stripped)
    if match:
        return f"{Path(match.group('path')).name}: {match.group('rest').strip()}"
    return re.sub(r"^Error:\s*", "", stripped)


class RenderError(Exception):
    """The request or the manuscript cannot be rendered."""


class ValidationError(Exception):
    """The converter rejected the generated slides."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class VerificationError(Exception):
    """The produced PPTX failed the structural contract."""

    def __init__(self, checks: dict[str, bool]) -> None:
        failed = ", ".join(name for name, ok in checks.items() if not ok)
        super().__init__(f"verification failed: {failed}")
        self.checks = checks


@dataclass
class DeckRequest:
    markdown: str
    aspect_ratio: str = "16:9"
    theme: str = "classic"
    max_bullets: int = DEFAULT_MAX_BULLETS
    name: str | None = None
    dry_run: bool = False
    author: str = "pptsvc"
    title: str | None = None

    def validate(self) -> None:
        if not self.markdown.strip():
            raise RenderError("markdown is empty")
        if self.aspect_ratio not in CANVAS:
            raise RenderError(
                f"unsupported aspect_ratio {self.aspect_ratio!r}; "
                f"expected one of {', '.join(CANVAS)}"
            )
        if not 1 <= self.max_bullets <= 12:
            raise RenderError("max_bullets must be between 1 and 12")
        if self.theme not in THEMES:
            raise RenderError(
                f"unsupported theme {self.theme!r}; "
                f"expected one of {', '.join(THEMES)}"
            )
        try:
            require_title(self.markdown)
        except ValueError as exc:
            raise RenderError(str(exc)) from exc


_semaphore: asyncio.Semaphore | None = None


def _limit(settings: Settings) -> asyncio.Semaphore:
    """One semaphore per process: Chromium is memory hungry, not CPU bound."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrency)
    return _semaphore


def _node_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    if settings.node_path:
        env["NODE_PATH"] = settings.node_path
    if settings.browsers_path:
        env["PLAYWRIGHT_BROWSERS_PATH"] = settings.browsers_path
    return env


def parse_converter_errors(stderr: str, stdout: str = "") -> list[str]:
    """Turn converter output into a flat list of human readable errors."""
    output = "\n".join(part for part in (stderr, stdout) if part).strip()
    if not output:
        return ["converter failed without diagnostics"]
    numbered = [match.group(1).strip() for match in map(NUMBERED_ERROR_RE.match, output.splitlines()) if match]
    if numbered:
        return [_clean(item) for item in numbered]
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith(("Error", "TypeError", "RangeError"))
    ]
    return [_clean(" ".join(lines))] if lines else [_clean(output.splitlines()[-1])]


async def _run_converter(
    request: DeckRequest,
    settings: Settings,
    slides_dir: Path,
    pptx_path: Path | None,
) -> None:
    command = [
        settings.node_bin,
        str(settings.converter),
        "--html_dir",
        str(slides_dir),
        "--layout",
        request.aspect_ratio,
        "--author",
        request.author,
        "--title",
        request.title or request.name or "Presentation",
    ]
    command += ["--validate"] if pptx_path is None else ["--output", str(pptx_path)]
    process = await asyncio.create_subprocess_exec(
        *command,
        env=_node_env(settings),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.timeout_seconds
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RenderError(
            f"converter timed out after {settings.timeout_seconds:g}s"
        ) from None
    if process.returncode:
        raise ValidationError(
            parse_converter_errors(
                stderr.decode("utf-8", errors="replace"),
                stdout.decode("utf-8", errors="replace"),
            )
        )


def _render_pages(
    request: DeckRequest, settings: Settings, scale: float
) -> dict[str, str]:
    try:
        return render_deck(
            request.markdown,
            request.aspect_ratio,
            request.max_bullets,
            settings.templates_dir,
            height_scale=scale,
            theme=request.theme,
        )
    except ValueError as exc:
        raise RenderError(str(exc)) from exc


def _write_slides(slides_dir: Path, pages: dict[str, str]) -> None:
    for stale in slides_dir.glob("slide*.html"):
        stale.unlink()
    for filename, content in pages.items():
        (slides_dir / filename).write_text(content, encoding="utf-8")


# Preview returns every page of realistic decks; the cap only guards a
# pathologically large manuscript from producing a huge JSON payload.
PREVIEW_MAX_PAGES = 60

_IMG_SRC_RE = re.compile(r'src="(assets/[^"?]+)"')
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp"}


def _inline_images(html_text: str, assets_dir: Path) -> str:
    """Rewrite relative image srcs to data URIs so iframe srcdoc previews
    (which have no base URL) show the real images the deck will contain."""

    def replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        f = assets_dir / Path(rel).name
        if f.is_file():
            mime = _MIME.get(f.suffix.lower())
            if mime:
                b64 = base64.b64encode(f.read_bytes()).decode("ascii")
                return f'src="data:{mime};base64,{b64}"'
        return match.group(0)

    return _IMG_SRC_RE.sub(replace, html_text)


def preview(request: DeckRequest, settings: Settings) -> dict[str, Any]:
    """Render the first pages to HTML only — no Chromium, no PPTX.

    The returned HTML is exactly what the converter would consume, so an
    iframe preview in the browser is a faithful style preview.
    """
    request.validate()
    if not (settings.templates_dir / request.theme / "section.css").is_file():
        raise RenderError(f"theme templates not found for {request.theme!r}")
    pages = _render_pages(request, settings, 1.0)
    width, height = CANVAS[request.aspect_ratio]
    return {
        "theme": request.theme,
        "aspect_ratio": request.aspect_ratio,
        "canvas": [width, height],
        "slides": len(pages),
        "pages": [
            {
                "file": name,
                "kind": "cover" if name == "slide01.html" else "section",
                "html": _inline_images(content, settings.assets_dir),
            }
            for name, content in list(pages.items())[:PREVIEW_MAX_PAGES]
        ],
    }


async def generate(request: DeckRequest, settings: Settings) -> dict[str, Any]:
    """Render one deck and return its result document."""
    request.validate()
    if not settings.converter.is_file():
        raise RenderError(f"converter not found at {settings.converter}")
    if not (settings.templates_dir / request.theme / "section.css").is_file():
        raise RenderError(f"theme templates not found for {request.theme!r}")

    task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    workdir = settings.output_dir / task_id
    slides_dir = workdir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    (workdir / "request.json").write_text(
        json.dumps(asdict(request), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (workdir / "manuscript.md").write_text(request.markdown, encoding="utf-8")

    timings: dict[str, int] = {}
    mark = time.perf_counter()
    pages = _render_pages(request, settings, RETRY_SCALES[0])
    _write_slides(slides_dir, pages)
    timings["render"] = int((time.perf_counter() - mark) * 1000)

    # Copy assets directory into the slides directory so that relative image
    # paths in the HTML (e.g. <img src="assets/photo.jpg">) resolve correctly.
    # The check is best-effort: if the source does not exist or fails to copy
    # the converter will report the missing image as a validation error.
    try:
        src_assets = Path(__file__).resolve().parent.parent / "assets"
        if src_assets.is_dir():
            dst_assets = slides_dir / "assets"
            if dst_assets.is_dir():
                shutil.rmtree(dst_assets)
            shutil.copytree(src_assets, dst_assets)
    except Exception:
        pass  # best-effort; converter will surface real missing-image errors

    pptx_path = None if request.dry_run else workdir / "answer.pptx"
    convert_error: ValidationError | None = None
    applied_scale = RETRY_SCALES[0]
    mark = time.perf_counter()
    for attempt, scale in enumerate(RETRY_SCALES):
        try:
            async with _limit(settings):
                await _run_converter(request, settings, slides_dir, pptx_path)
            convert_error = None
            applied_scale = scale
            break
        except ValidationError as exc:
            convert_error = exc
            overflow = any(OVERFLOW_RE.search(err) for err in exc.errors)
            if attempt == len(RETRY_SCALES) - 1 or not overflow:
                break
            pages = _render_pages(request, settings, RETRY_SCALES[attempt + 1])
            _write_slides(slides_dir, pages)
    timings["convert"] = int((time.perf_counter() - mark) * 1000)
    if convert_error is not None:
        errors = list(convert_error.errors)
        if any(OVERFLOW_RE.search(err) for err in errors):
            errors.append(
                "a single bullet is too tall to fit on one page; "
                "split it into shorter bullets"
            )
        raise ValidationError(errors)

    result: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "slides": len(pages),
        "aspect_ratio": request.aspect_ratio,
        "dry_run": request.dry_run,
        "artifact": None,
        "artifact_bytes": None,
        "checks": {},
        "height_scale": applied_scale,
        "timings_ms": timings,
    }
    if pptx_path is not None:
        mark = time.perf_counter()
        checks = verify_pptx(
            pptx_path, len(pages), request.aspect_ratio, request.markdown
        )
        timings["verify"] = int((time.perf_counter() - mark) * 1000)
        result["checks"] = checks
        result["artifact"] = f"{task_id}/answer.pptx"
        result["artifact_bytes"] = pptx_path.stat().st_size
        if not checks["ok"]:
            raise VerificationError(checks["checks"])
    result["workdir"] = task_id
    (workdir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def load_result(settings: Settings, task_id: str) -> dict[str, Any] | None:
    """Read a previous result document, rejecting path traversal."""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", task_id):
        return None
    path = settings.output_dir / task_id / "result.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
