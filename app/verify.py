"""Structural verification of a generated PPTX. No model calls, no PowerPoint.

Confirms the artifact is a well formed PPTX, that the slide count and canvas
ratio match the request, and that the manuscript copy really landed as editable
text rather than as a screenshot.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
EXPECTED_RATIO = {"16:9": 16 / 9, "4:3": 4 / 3, "A1": 23.39 / 33.11}
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+>]|\d+[.)])\s+")
IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)(?:\s*\{[^}]*\})?\s*$")
_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*")


def _slide_names(archive: zipfile.ZipFile) -> list[str]:
    names = [
        name
        for name in archive.namelist()
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
    ]
    return sorted(names, key=lambda name: int(re.search(r"(\d+)", name.split("/")[-1]).group(1)))


def _slide_text(archive: zipfile.ZipFile, name: str) -> str:
    root = ET.fromstring(archive.read(name))
    return "".join(node.text or "" for node in root.iter(f"{{{NS['a']}}}t"))


def expected_copy(markdown: str) -> list[str]:
    """Manuscript lines that must survive into the deck as editable text.

    List markers are dropped: bullets and numbers become native PPTX list
    formatting, so they are intentionally absent from the slide XML.
    Image lines ('![alt](src)') are media, not copy — until image rendering
    ships, the renderer skips them and verification must not expect them.
    """
    return [
        _BOLD_MD_RE.sub(r"\1", LIST_MARKER_RE.sub("", line)).strip()
        for line in markdown.splitlines()
        if line.strip()
        and not line.startswith("#")
        and line.strip() != "---"
        and not IMAGE_LINE_RE.match(line)
    ]


def verify_pptx(
    pptx: Path,
    slides: int,
    aspect_ratio: str = "16:9",
    markdown: str | None = None,
) -> dict[str, Any]:
    with zipfile.ZipFile(pptx) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"corrupt PPTX member: {corrupt}")
        names = _slide_names(archive)
        root = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = root.find("p:sldSz", NS)
        if size is None:
            raise ValueError("presentation.xml has no sldSz entry")
        cx, cy = int(size.attrib["cx"]), int(size.attrib["cy"])
        ratio = cx / cy
        texts = {name: _slide_text(archive, name) for name in names}
        media = [
            name
            for name in archive.namelist()
            if name.startswith("ppt/media/") and not name.endswith("/")
        ]
        image_only = 0
        for name in names:
            root_slide = ET.fromstring(archive.read(name))
            pictures = len(list(root_slide.iter(f"{{{NS['p']}}}pic")))
            if pictures and len(texts[name].strip()) < 10:
                image_only += 1

    expected_ratio = EXPECTED_RATIO.get(aspect_ratio)
    checks: dict[str, bool] = {
        "slide_count": len(names) == slides,
        "aspect_ratio": expected_ratio is not None and abs(ratio - expected_ratio) < 0.03,
        "text_is_editable": bool(names) and all(texts[name].strip() for name in names),
        "not_image_only": image_only == 0,
    }
    report: dict[str, Any] = {
        "ok": False,
        "slides": len(names),
        "expected_slides": slides,
        "canvas_inches": [round(cx / 914400, 3), round(cy / 914400, 3)],
        "aspect_ratio": round(ratio, 4),
        "media_files": len(media),
        "image_only_slides": image_only,
        "checks": checks,
    }
    if markdown is not None:
        normalise = lambda value: re.sub(r"\s+", "", value)  # noqa: E731
        deck_text = normalise("".join(texts.values()))
        missing = [item for item in expected_copy(markdown) if normalise(item) not in deck_text]
        checks["all_copy_present"] = not missing
        report["missing_copy"] = missing[:5]
    report["ok"] = all(checks.values())
    return report
