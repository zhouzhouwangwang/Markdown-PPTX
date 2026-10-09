"""Runtime configuration for the standalone deck generation service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    """Filesystem and process settings, all overridable through the environment."""

    output_dir: Path
    converter: Path
    templates_dir: Path
    assets_dir: Path
    node_bin: str
    node_path: str | None
    browsers_path: str | None
    max_concurrency: int
    timeout_seconds: float
    keep_html: bool

    @classmethod
    def load(cls) -> "Settings":
        converter = _env_path(
            "PPTSVC_CONVERTER", SERVICE_ROOT / "vendor/html2pptx/html2pptx_cli.js"
        )
        node_path = os.environ.get("PPTSVC_NODE_PATH") or os.environ.get("NODE_PATH")
        if not node_path:
            # The vendored tree keeps its own node_modules for host runs; the image
            # installs them once at /opt/pptsvc so mounted code never hides them.
            local = SERVICE_ROOT / "vendor/html2pptx/node_modules"
            node_path = str(local) if local.is_dir() else None
        return cls(
            output_dir=_env_path("PPTSVC_OUTPUT_DIR", SERVICE_ROOT / "output"),
            converter=converter,
            templates_dir=_env_path(
                "PPTSVC_TEMPLATES_DIR", SERVICE_ROOT / "app/templates"
            ),
            assets_dir=_env_path("PPTSVC_ASSETS_DIR", SERVICE_ROOT / "assets"),
            node_bin=os.environ.get("PPTSVC_NODE", "node"),
            node_path=node_path,
            browsers_path=os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
            max_concurrency=max(1, int(os.environ.get("PPTSVC_MAX_CONCURRENCY", "2"))),
            timeout_seconds=float(os.environ.get("PPTSVC_TIMEOUT_SECONDS", "300")),
            keep_html=_env_flag("PPTSVC_KEEP_HTML", True),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
