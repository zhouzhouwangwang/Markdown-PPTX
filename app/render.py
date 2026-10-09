"""Deterministic Markdown -> HTML slide rendering.

Standard library only, no model calls. The generated HTML satisfies the
converter's hard constraints: an exact pixel canvas, every piece of text inside
<p>/<h1>-<h6>/<ul>/<ol>, and text blocks clear of the 0.5 inch bottom margin.
Visual style lives in app/templates/*.css so it can be changed without touching
this module.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CANVAS = {"16:9": (1280, 720), "4:3": (960, 720), "A1": (2244, 3178)}
DEFAULT_MAX_BULLETS = 6
SIDE_MARGIN = 90
COVER_MARGIN = 110
# Vertical geometry — keep in sync with templates/section.css.
CONTENT_TOP = 236.0  # .content { top }
BOTTOM_MARGIN_PX = 48.0  # converter contract: text ends >= 0.5in above the bottom
SAFETY_FACTOR = 1.05  # estimator conservatism vs real font metrics
UNIT_WIDE = 1.0  # CJK / fullwidth glyph width in em
UNIT_ASCII = 0.58  # latin letters and digits in a CJK typeface
UNIT_NARROW = 0.40  # spaces and light punctuation
NARROW_CHARS = set(" \t.,:;'\"!?()[]{}/\\|·-_=+*<>@#$%^&`~")


@dataclass(frozen=True)
class ThemeMetrics:
    """Vertical metrics of a theme — must match its section.css exactly."""

    layout: str  # "list" (plain bullets) or "cards" (one card per bullet)
    content_top: float
    body_font: float
    body_line: float
    li_margin: float
    p_margin: float
    h3_font: float
    h3_margin: float
    card_pad: float = 16.0
    card_gap: float = 14.0
    card_num_zone: float = 76.0
    card_inner_gap: float = 18.0
    # Reserved bottom chrome (footer bar). The content budget, pin zone and
    # region backgrounds all stop above it — pages cannot cover it by
    # construction. No shipped theme sets a footer yet.
    footbar_height: float = 0.0
    # Reserved top chrome (nav/menu bar). The pin grid starts below it, so a
    # pin=t/tr sticker can never cover the navigation.
    topbar_height: float = 0.0


THEMES: dict[str, ThemeMetrics] = {
    "classic": ThemeMetrics(
        "list", 236.0, 26.0, 1.45, 22.0, 20.0, 28.0, 14.0, topbar_height=12.0
    ),
    "swiss": ThemeMetrics(
        "list", 220.0, 25.0, 1.5, 20.0, 18.0, 27.0, 12.0, topbar_height=12.0
    ),
    "midnight": ThemeMetrics(
        "list", 230.0, 25.0, 1.5, 21.0, 18.0, 27.0, 12.0, topbar_height=12.0
    ),
}

# Generated themes "{style}.{industry}": CSS is emitted by tools/build_themes.py
# from industry palette tokens (offline); all cards.* share the skeleton metrics.
INDUSTRIES = ("tech", "finance", "edu", "energy", "gov", "med")
CARDS_METRICS = ThemeMetrics(
    layout="cards",
    content_top=214.0,
    body_font=24.0,
    body_line=1.5,
    li_margin=14.0,
    p_margin=16.0,
    h3_font=27.0,
    h3_margin=12.0,
    topbar_height=12.0,
)
SITE_METRICS = ThemeMetrics(
    layout="site",
    content_top=184.0,
    body_font=24.0,
    body_line=1.5,
    li_margin=18.0,
    p_margin=16.0,
    h3_font=26.0,
    h3_margin=12.0,
    topbar_height=74.0,  # nav bar 72px + 2px border
)
for _style, _metrics in (("cards", CARDS_METRICS), ("site", SITE_METRICS)):
    for _industry in INDUSTRIES:
        THEMES[f"{_style}.{_industry}"] = _metrics
DEFAULT_THEME = "classic"

BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
IMAGE_RE = re.compile(
    r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)"
    r"(?:\s*\{(?P<opts>[^}]*)\})?\s*$"
)
GLUED_HEADING_RE = re.compile(r"^#{1,6}\S")


def parse_markdown(text: str) -> dict[str, Any]:
    """Split a manuscript into a cover plus one entry per '##' section."""
    deck: dict[str, Any] = {"title": "", "lead": "", "sections": []}
    section: dict[str, Any] | None = None
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        block = {"kind": "p", "text": " ".join(paragraph).strip()}
        paragraph.clear()
        if section is None:
            if not deck["lead"]:
                deck["lead"] = block["text"]
        else:
            section["blocks"].append(block)

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped == "---":
            flush()
            continue
        if stripped.startswith("### "):
            flush()
            if section is not None:
                section["blocks"].append({"kind": "h3", "text": stripped[4:].strip()})
            continue
        if stripped.startswith("## "):
            flush()
            section = {"title": stripped[3:].strip(), "blocks": []}
            deck["sections"].append(section)
            continue
        if stripped.startswith("# "):
            flush()
            deck["title"] = stripped[2:].strip()
            continue
        if stripped.startswith("> "):
            flush()
            text_value = stripped[2:].strip()
            if section is None:
                deck["lead"] = text_value
            else:
                section["blocks"].append({"kind": "lead", "text": text_value})
            continue
        image = IMAGE_RE.match(line)
        if image:
            flush()
            src = image.group("src").strip()
            alt = image.group("alt").strip()
            opts = (image.group("opts") or "").strip()
            if alt == "logo":
                deck["logo"] = src
            elif alt == "background" and section is None:
                # Preamble background = deck-wide: every page gets this backdrop.
                deck["background"] = {"src": src, "opts": opts}
            elif section is not None:
                # Inside a ## section: alt="background" becomes THIS section's
                # page background (overrides the global one on its pages only).
                section["blocks"].append(
                    {"kind": "image", "src": src, "alt": alt, "opts": opts}
                )
            else:
                # Cover figures: every plain image before the first ## flows
                # into the cover figure zone (size/pos opts honoured).
                deck.setdefault("cover_images", []).append(
                    {"src": src, "alt": alt, "opts": opts}
                )
            continue
        bullet, ordered = BULLET_RE.match(line), ORDERED_RE.match(line)
        if bullet or ordered:
            flush()
            match = bullet or ordered
            kind = "bullet" if bullet else "number"
            if section is None:
                section = {"title": deck["title"], "blocks": []}
                deck["sections"].append(section)
            last = section["blocks"][-1] if section["blocks"] else None
            if last is not None and last["kind"] == kind:
                last["items"].append(match.group(1).strip())
            else:
                section["blocks"].append(
                    {"kind": kind, "items": [match.group(1).strip()]}
                )
            continue
        paragraph.append(stripped)

    flush()
    deck["sections"] = [item for item in deck["sections"] if item["blocks"]]
    return deck


def require_title(markdown: str) -> dict[str, Any]:
    """Parse a manuscript and enforce the level-1 title contract.

    Markers need a trailing space ('# 标题', '- 要点'). When a heading is
    glued to its text ('#标题') the error carries a concrete fix instead of
    a generic complaint — that is the most common manuscript mistake.
    """
    deck = parse_markdown(markdown)
    if deck["title"]:
        return deck
    glued = next(
        (
            line.strip()
            for line in markdown.splitlines()
            if GLUED_HEADING_RE.match(line.strip())
        ),
        None,
    )
    hint = ""
    if glued:
        depth = len(glued) - len(glued.lstrip("#"))
        hint = (
            " Heading markers must be followed by a space, "
            f"e.g. '{glued[:depth]} {glued[depth:]}'."
        )
    raise ValueError("manuscript must start with a level-1 title ('# ...')" + hint)


def _units(text: str) -> float:
    """Approximate rendered width in em: CJK glyphs are full width."""
    total = 0.0
    for ch in text:
        if ch in NARROW_CHARS:
            total += UNIT_NARROW
        elif ord(ch) >= 0x2E80:
            total += UNIT_WIDE
        else:
            total += UNIT_ASCII
    return total


def estimate_lines(text: str, font_px: float, width_px: float) -> int:
    """Deterministic wrapped-line estimate, mildly conservative."""
    if not text:
        return 1
    per_line = max(1.0, width_px / font_px)
    return max(1, math.ceil(_units(text) / per_line))


def _li_height(item: str, width: float, m: ThemeMetrics | None = None) -> float:
    m = m or THEMES[DEFAULT_THEME]
    if m.layout == "cards":
        # One rounded card per bullet: padding + wrapped text + inter-card gap.
        text_width = max(
            200.0, width - 2 * m.card_pad - m.card_num_zone - m.card_inner_gap
        )
        return (
            2 * m.card_pad
            + estimate_lines(item, m.body_font, text_width) * m.body_font * m.body_line
            + m.card_gap
        )
    return (
        estimate_lines(item, m.body_font, width) * m.body_font * m.body_line
        + m.li_margin
    )


_IMG_SIZES = {"quarter": 0.25, "half": 0.5, "full": 1.0}
_FIG_CAPTION_FONT = 16.0


def _parse_img_opts(opts: str) -> dict[str, str]:
    """'{half, left}' -> {'half': '', 'left': ''}; '{width=60%}' -> {'width': '60%'}."""
    parsed: dict[str, str] = {}
    for part in opts.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "=" in part:
            key, _, value = part.partition("=")
            parsed[key.strip()] = value.strip()
        else:
            parsed[part] = ""
    return parsed


def _image_size(src: str) -> tuple[int, int] | None:
    """Pixel dimensions of an assets/ image (None when unreadable/missing)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    f = Path(__file__).resolve().parents[1] / "assets" / Path(src).name
    if not f.is_file():
        return None
    try:
        with Image.open(f) as img:
            return img.size
    except Exception:
        return None


def _figure_layout(
    block: dict[str, Any], content_width: float
) -> tuple[int, int, str, str]:
    """(width_px, height_px, margin_css, pos) for an in-flow figure block.

    Opts accept both bare keywords ('{half,left}') and key=value
    ('{width=60%,pos=right}'); the height follows the image's true aspect
    ratio so pagination and the browser agree.
    """
    opts = _parse_img_opts(block.get("opts", ""))
    size = opts.get("size") or next((k for k in _IMG_SIZES if k in opts), "") or "half"
    frac = _IMG_SIZES.get(size, 0.5)
    if "width" in opts:
        try:
            frac = max(0.05, min(1.0, float(opts["width"].rstrip("%")) / 100.0))
        except ValueError:
            pass
    w = max(40.0, content_width * frac)
    dims = _image_size(block["src"])
    aspect = (dims[1] / dims[0]) if dims and dims[0] else 0.62
    h = w * aspect
    pos = (
        opts.get("pos")
        or next((p for p in ("left", "center", "right") if p in opts), "")
        or "center"
    )
    margin = (
        "0 auto 0 0" if pos == "left" else ("0 0 0 auto" if pos == "right" else "0 auto")
    )
    return int(w), int(h), margin, pos


_PIN_ANCHORS = ("tl", "t", "tr", "l", "c", "r", "bl", "b", "br")
_CELL_POS = {
    "tl": (0, 0), "t": (0, 1), "tr": (0, 2),
    "l": (1, 0), "c": (1, 1), "r": (1, 2),
    "bl": (2, 0), "b": (2, 1), "br": (2, 2),
}


def _pin_anchors(fig: dict[str, Any]) -> list[str]:
    """pin= accepts a '+' chain of grid cells (single value = classic pin)."""
    raw = _parse_img_opts(fig.get("opts", "")).get("pin", "c")
    cells = [c for c in (p.strip() for p in raw.split("+")) if c in _CELL_POS]
    return cells or ["c"]


def _cells_contiguous(cells: list[str]) -> bool:
    """True when the selected cells form one 4-adjacency-connected group."""
    if len(cells) <= 1:
        return True
    pos = {_CELL_POS[c] for c in cells}
    start = next(iter(pos))
    seen, stack = {start}, [start]
    while stack:
        r, c = stack.pop()
        for nb in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if nb in pos and nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return len(seen) == len(pos)


def _cells_rectangular(cells: list[str]) -> bool:
    """True when the selection FILLS its own bounding box (row×col count ==
    cell count) — e.g. a full column/row or a 2x2 block. Only such exact
    rectangles use bounding-box display; connected-but-L/T-shaped selections
    fall back to fragment mode (their bbox would cover unselected cells)."""
    if len(cells) <= 1:
        return True
    rows = {_CELL_POS[c][0] for c in cells}
    cols = {_CELL_POS[c][1] for c in cells}
    return len(rows) * len(cols) == len(set(cells)) and _cells_contiguous(cells)


def _crop_fragment(
    src: str, cell: str, rel: tuple[float, float, float, float], size_px: tuple[int, int]
) -> str:
    """Crop one cell's visible part of the (virtual) full-grid image.
    Deterministic cache: {stem}.cell{cell}{W}x{H}{ext}."""
    try:
        from PIL import Image
    except ImportError:
        return src
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    source = assets_dir / Path(src).name
    if not source.is_file():
        return src
    out_name = f"{source.stem}.cell{cell}{size_px[0]}x{size_px[1]}{source.suffix}"
    out = assets_dir / out_name
    if not out.is_file():
        img = Image.open(source).convert("RGB")
        w, h = img.size
        crop = img.crop(
            (
                max(0, int(rel[0] * w)),
                max(0, int(rel[1] * h)),
                min(w, int(rel[2] * w)),
                min(h, int(rel[3] * h)),
            )
        )
        crop.save(out, quality=88)
    return f"assets/{out_name}"


_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*")


def _esc(text: str) -> str:
    """Escape text for HTML and map **bold** markdown to real <strong> tags —
    the converter renders those as bold text runs (inline formatting is
    supported; a literal leading '*' would be eaten as a list marker)."""
    return _BOLD_MD_RE.sub(r"<strong>\1</strong>", html.escape(text))


def _is_pinned(fig: dict[str, Any]) -> bool:
    return "pin" in _parse_img_opts(fig.get("opts", ""))


def _is_fill(fig: dict[str, Any]) -> bool:
    return "fill" in _parse_img_opts(fig.get("opts", ""))


def _pin_layout(
    fig: dict[str, Any], region_w: float, region_h: float
) -> tuple[int, int, int, int]:
    """Pinned ("sticker") image geometry inside a region box.

    pin=tl/t/tr/l/c/r/bl/b/br aligns the image's corresponding corner/edge to
    the region's (CSS background-position semantics); dx/dy nudge in px from
    the anchor (positive = right/down). The result is clamped into the region
    so a pin can never cover the footbar or leave the canvas.
    """
    opts = _parse_img_opts(fig.get("opts", ""))
    frac = 0.3
    if "w" in opts:
        try:
            frac = max(0.05, min(1.0, float(opts["w"].rstrip("%")) / 100.0))
        except ValueError:
            pass
    w = region_w * frac
    dims = _image_size(fig["src"])
    aspect = (dims[1] / dims[0]) if dims and dims[0] else 0.62
    h = w * aspect
    if h > region_h > 0:  # keep aspect, fit height first
        scale = region_h / h
        h *= scale
        w *= scale
    pin = opts.get("pin", "c")
    if pin not in _PIN_ANCHORS:
        pin = "c"
    left = (
        0.0 if pin.endswith("l") else (region_w - w if pin.endswith("r") else (region_w - w) / 2)
    )
    top = (
        0.0 if pin.startswith("t") else (region_h - h if pin.startswith("b") else (region_h - h) / 2)
    )
    try:
        left += float(opts.get("dx", "0") or 0)
    except ValueError:
        pass
    try:
        top += float(opts.get("dy", "0") or 0)
    except ValueError:
        pass
    left = max(0.0, min(left, max(0.0, region_w - w)))
    top = max(0.0, min(top, max(0.0, region_h - h)))
    return int(w), int(h), int(left), int(top)


def _pin_z(fig: dict[str, Any]) -> int:
    """Layer of a pinned sticker. Fragment mode (non-contiguous multi-cell)
    defaults to 0 — under the text; single/contiguous default to 10."""
    opts = _parse_img_opts(fig.get("opts", ""))
    if "z" in opts:
        try:
            return int(float(opts["z"]))
        except (TypeError, ValueError):
            return 10
    cells = _pin_anchors(fig)
    if len(cells) > 1 and not _cells_rectangular(cells):
        return 0
    return 10


def _split_by_z(
    figs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(under-text z<=0, over-text z>0), each stably sorted by z —
    markdown order breaks ties, keeping output deterministic."""
    under = sorted((f for f in figs if _pin_z(f) <= 0), key=_pin_z)
    over = sorted((f for f in figs if _pin_z(f) > 0), key=_pin_z)
    return under, over


def _fig_pos(block: dict[str, Any]) -> str:
    opts = _parse_img_opts(block.get("opts", ""))
    return (
        opts.get("pos")
        or next((p for p in ("left", "center", "right") if p in opts), "")
        or "center"
    )


def _merge_fig_rows(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair consecutive flow figures '{half,left}' + '{half,right}' into one
    side-by-side row block so pagination and the emitter see a single unit."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if (
            b.get("kind") == "image"
            and nxt
            and nxt.get("kind") == "image"
            and not _is_pinned(b)
            and not _is_pinned(nxt)
            and _fig_pos(b) == "left"
            and _fig_pos(nxt) == "right"
        ):
            out.append({"kind": "figrow", "items": [b, nxt]})
            i += 2
            continue
        out.append(b)
        i += 1
    return out


def _pinned_html(
    figs: list[dict[str, Any]],
    left_px: float,
    top_px: float,
    region_w: float,
    region_h: float,
    under: bool = False,
) -> str:
    """One absolute overlay zone carrying pinned figures of one layer."""
    if not figs:
        return ""
    cls = "pinzone under" if under else "pinzone"
    parts = [
        f'  <div class="{cls}" style="position:absolute;left:{int(left_px)}px;'
        f'top:{int(top_px)}px;width:{int(region_w)}px;height:{int(region_h)}px;">'
    ]
    for fig in figs:
        cells = _pin_anchors(fig)
        dims = _image_size(fig["src"])
        aspect = (dims[1] / dims[0]) if dims and dims[0] else 0.62
        dx = dy = 0.0
        opts = _parse_img_opts(fig.get("opts", ""))
        try:
            dx = float(opts.get("dx", "0") or 0)
            dy = float(opts.get("dy", "0") or 0)
        except ValueError:
            pass
        if len(cells) == 1:
            w, h, l, t = _pin_layout(fig, region_w, region_h)
            parts.append(
                f'    <img src="{html.escape(fig["src"])}" alt="{html.escape(fig.get("alt", ""))}" '
                f'style="position:absolute;left:{l}px;top:{t}px;width:{w}px;height:{h}px;">'
            )
            continue
        rows = [_CELL_POS[c][0] for c in cells]
        cols = [_CELL_POS[c][1] for c in cells]
        if _cells_rectangular(cells):
            # One image contain-fit into the selection's bounding rect.
            x0 = min(cols) / 3 * region_w
            x1 = (max(cols) + 1) / 3 * region_w
            y0 = min(rows) / 3 * region_h
            y1 = (max(rows) + 1) / 3 * region_h
            bw, bh = x1 - x0, y1 - y0
            w = min(bw, bh / aspect) if aspect else bw
            h = w * aspect if aspect else bh
            l = x0 + (bw - w) / 2 + dx
            t = y0 + (bh - h) / 2 + dy
            l = max(0.0, min(l, max(0.0, region_w - w)))
            t = max(0.0, min(t, max(0.0, region_h - h)))
            parts.append(
                f'    <img src="{html.escape(fig["src"])}" alt="{html.escape(fig.get("alt", ""))}" '
                f'style="position:absolute;left:{int(l)}px;top:{int(t)}px;'
                f'width:{int(w)}px;height:{int(h)}px;">'
            )
            continue
        # Fragment mode: a virtual image at FULL grid scale (w= is a single/
        # rectangular-pin knob and is intentionally ignored here — a shrunken
        # centred image would never reach edge cells), visible only in the
        # selected cells via Pillow-cropped fragments.
        vw = region_w
        vh = vw * aspect
        if vh > region_h:
            vh = region_h
            vw = vh / aspect if aspect else region_w
        vl = max(0.0, min((region_w - vw) / 2 + dx, region_w - vw))
        vt = max(0.0, min((region_h - vh) / 2 + dy, region_h - vh))
        for cell in cells:
            r, c = _CELL_POS[cell]
            cx0, cx1 = c / 3 * region_w, (c + 1) / 3 * region_w
            cy0, cy1 = r / 3 * region_h, (r + 1) / 3 * region_h
            ix0, iy0 = max(cx0, vl), max(cy0, vt)
            ix1, iy1 = min(cx1, vl + vw), min(cy1, vt + vh)
            if ix1 - ix0 < 8 or iy1 - iy0 < 8:
                continue  # cell not covered by the virtual image
            rel = (
                (ix0 - vl) / vw if vw else 0,
                (iy0 - vt) / vh if vh else 0,
                (ix1 - vl) / vw if vw else 1,
                (iy1 - vt) / vh if vh else 1,
            )
            frag = _crop_fragment(
                fig["src"], cell, rel, (int(ix1 - ix0), int(iy1 - iy0))
            )
            parts.append(
                f'    <img src="{html.escape(frag)}" alt="{html.escape(fig.get("alt", ""))}" '
                f'style="position:absolute;left:{int(ix0)}px;top:{int(iy0)}px;'
                f'width:{int(ix1 - ix0)}px;height:{int(iy1 - iy0)}px;">'
            )
    parts.append("  </div>")
    return "\n".join(parts)


def _block_height(
    block: dict[str, Any], width: float, m: ThemeMetrics | None = None
) -> float:
    m = m or THEMES[DEFAULT_THEME]
    kind = block["kind"]
    if kind == "figrow":
        # Side-by-side row: height is the MAX of members, not the sum.
        return max(
            _block_height(item, width, m) for item in block["items"]
        )
    if kind == "image":
        if _is_pinned(block):
            return 0.0  # stickers do not consume pagination budget
        if _is_fill(block):
            return 0.0  # fill sizing is budget-aware, handled in chunk loop
        if block.get("alt") == "background":
            return 0.0  # section-local backdrop: zero pagination cost
        _, img_h, _, _ = _figure_layout(block, width)
        caption = block.get("alt") and (
            estimate_lines(block["alt"], _FIG_CAPTION_FONT, width)
            * _FIG_CAPTION_FONT
            * 1.4
            + 8.0
        )
        return img_h + (caption or 0.0)
    if kind in {"bullet", "number"}:
        return sum(_li_height(item, width, m) for item in block["items"])
    if kind == "h3":
        return (
            estimate_lines(block["text"], m.h3_font, width) * m.h3_font * 1.5
            + m.h3_margin
        )
    return (
        estimate_lines(block["text"], m.body_font, width) * m.body_font * m.body_line
        + m.p_margin
    )


def _group_units(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group blocks into pagination units: an h3 heading and everything after
    it (until the next h3) form ONE unit — the heading is never separated from
    its content, and page cuts prefer unit boundaries. A nested h4 joins the
    enclosing h3 unit (content integrity adapts to deeper outlines)."""
    units: list[list[dict[str, Any]]] = []
    group: list[dict[str, Any]] | None = None
    for b in blocks:
        if b["kind"] == "h3":
            if group:
                units.append(group)
            group = [b]
        elif group is not None and not (
            b["kind"] == "image" and (_is_fill(b) or b.get("alt") == "background")
        ):
            group.append(b)
        else:
            if group:
                units.append(group)
                group = None
            units.append([b])
    if group:
        units.append(group)
    return units


def _unit_height(unit: list[dict[str, Any]], width: float, m: ThemeMetrics) -> float:
    return sum(_block_height(b, width, m) for b in unit) if width else 0.0


def _unit_bullets(unit: list[dict[str, Any]]) -> int:
    return sum(len(b["items"]) for b in unit if b["kind"] in {"bullet", "number"})


def chunk_sections(
    section: dict[str, Any],
    limit: int,
    width: float = 0.0,
    height: float = 0.0,
    scale: float = 1.0,
    metrics: ThemeMetrics | None = None,
) -> list[dict[str, Any]]:
    """Split a section so no page breaks the converter's contracts.

    Two caps apply per page: at most `limit` list items, and an estimated
    content height that fits above the 0.5in bottom margin. Counting items
    alone cannot guarantee the height one — six two-line bullets overflow
    a 16:9 slide while six one-line bullets fit. `scale` shrinks the height
    budget; the pipeline retries with smaller scales when real font metrics
    still overflow.
    """
    m = metrics or THEMES[DEFAULT_THEME]
    budget = float("inf")
    if width and height:
        budget = max(
            120.0,
            (height - BOTTOM_MARGIN_PX - m.footbar_height - m.content_top)
            * scale
            / SAFETY_FACTOR,
        )

    pages: list[dict[str, Any]] = []
    title = section["title"]
    current: dict[str, Any] = {"title": title, "blocks": []}
    used, count = 0.0, 0

    def flush() -> None:
        nonlocal current, used, count
        pages.append(current)
        current = {"title": f"{title}（续）", "blocks": []}
        used, count = 0.0, 0

    def fits(item: str, base: float) -> bool:
        return base + _li_height(item, width, m) <= budget

    for unit in _group_units(section["blocks"]):
        # Atomic fast path: a heading-group that fits a page in one piece is
        # placed whole — page cuts land BETWEEN groups, never inside.
        unit_h, unit_n = _unit_height(unit, width, m), _unit_bullets(unit)
        if (
            unit[0]["kind"] == "h3"
            and unit_h <= budget
            and unit_n <= limit
        ):
            if current["blocks"] and (used + unit_h > budget or count + unit_n > limit):
                flush()
            current["blocks"].extend(unit)
            used += unit_h
            count += unit_n
            continue
        # Oversized group (or loose pre-heading blocks): flow block by block,
        # with an orphan guard — an h3 only lands where its first item fits too.
        for block in unit:
            if block["kind"] == "h3":
                bh = _block_height(block, width, m) if width else 0.0
                nxt_items = next(
                    (b["items"] for b in unit if b["kind"] in {"bullet", "number"}),
                    None,
                )
                nxt_h = (
                    _li_height(nxt_items[0], width, m)
                    if nxt_items and width and nxt_items
                    else 0.0
                )
                if current["blocks"] and (count >= limit or used + bh + nxt_h > budget):
                    flush()
                current["blocks"].append(block)
                used += bh
                continue
            if block["kind"] == "image" and _is_fill(block):
                # z<=0: content-region backdrop under text, zero budget.
                if _pin_z(block) <= 0:
                    current["blocks"].append({**block, "_fill_z0": True})
                    continue
                # z>0: swallow the remaining page height; content after flows on.
                rem = max(120.0, budget - used) if width else 300.0
                current["blocks"].append({**block, "_fill_h": min(rem, budget)})
                used = budget
                continue
            if block["kind"] not in {"bullet", "number"}:
                block_height = _block_height(block, width, m) if width else 0.0
                if current["blocks"] and used + block_height > budget:
                    flush()
                current["blocks"].append(block)
                used += block_height
                continue
            pending = list(block["items"])
            while pending:
                if current["blocks"] and (count >= limit or not fits(pending[0], used)):
                    flush()
                take = [pending.pop(0)]
                taken_height = _li_height(take[0], width, m) if width else 0.0
                while (
                    pending
                    and count + len(take) < limit
                    and fits(pending[0], used + taken_height)
                ):
                    taken_height += _li_height(pending[0], width, m) if width else 0.0
                    take.append(pending.pop(0))
                current["blocks"].append({**block, "items": take})
                used += taken_height
                count += len(take)
                if pending:
                    flush()
    if current["blocks"]:
        pages.append(current)
    return pages


def _load_css(templates_dir: Path, name: str, width: int, height: int) -> str:
    """Fill canvas placeholders. Values are bare numbers: every template
    writes the unit itself ('{{CONTENT_WIDTH}}px')."""
    template = (templates_dir / name).read_text(encoding="utf-8")
    return (
        template.replace("{{WIDTH}}", str(width))
        .replace("{{HEIGHT}}", str(height))
        .replace("{{CONTENT_WIDTH}}", str(width - 2 * SIDE_MARGIN))
        .replace("{{WRAP_WIDTH}}", str(width - 2 * COVER_MARGIN))
        .replace("{{SUB_WIDTH}}", str(width - 380))
    )


def _document(css: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="zh">\n<head>\n<meta charset="utf-8">\n'
        f"<style>\n{css}\n</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def _bake_background(src: str, opts: str, width: int, height: int) -> str:
    """Resize/crop a background image to the exact canvas and bake a dark
    overlay so slide text always sits on an even surface. Effects are baked
    offline (never CSS filters — the converter drops those); the output name
    is deterministic so identical inputs reuse the cache."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # Pillow optional: fall back to the raw image
        return src
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    source = assets_dir / Path(src).name
    if not source.is_file():
        return src  # the converter reports the missing file precisely
    dim = 0.5
    for part in opts.split(","):
        if part.strip().startswith("dim="):
            try:
                dim = max(0.0, min(0.9, float(part.split("=", 1)[1])))
            except ValueError:
                pass
    out_name = f"{source.stem}.bg{width}x{height}d{int(dim * 100)}{source.suffix}"
    out = assets_dir / out_name
    if not out.is_file():
        img = Image.open(source).convert("RGB")
        img = ImageOps.fit(img, (width, height), method=Image.LANCZOS)
        img = Image.blend(img, Image.new("RGB", (width, height), (0, 0, 0)), dim)
        img.save(out, quality=88)
    return f"assets/{out_name}"


def _background_html(
    deck: dict[str, Any],
    width: int,
    height: int,
    content_box: tuple[float, float, float, float] | None = None,
) -> str:
    """Deck background <img>. Default: full canvas. With {region=content}
    (and a content_box supplied by section pages) the image is baked cropped
    to the content region and stops above the footbar by construction."""
    bg = deck.get("background")
    if not bg:
        return ""
    if content_box is not None and "content" == _parse_img_opts(
        bg.get("opts", "")
    ).get("region"):
        x, y, w, h = content_box
        src = _bake_background(bg["src"], bg.get("opts", ""), int(w), int(h))
        return (
            f'  <img src="{html.escape(src)}" alt="" '
            f'style="position:absolute;left:{int(x)}px;top:{int(y)}px;'
            f'width:{int(w)}px;height:{int(h)}px;">\n'
        )
    src = _bake_background(bg["src"], bg.get("opts", ""), width, height)
    return (
        f'  <img src="{html.escape(src)}" alt="" '
        f'style="position:absolute;left:0;top:0;width:{width}px;height:{height}px;">\n'
    )


def _logo_html(deck: dict[str, Any], site: bool) -> str:
    logo = deck.get("logo")
    if not logo:
        return ""
    style = (
        "position:absolute;left:40px;top:16px;height:40px;"
        if site
        else "position:absolute;right:90px;top:36px;height:44px;"
    )
    return f'  <img src="{html.escape(logo)}" alt="logo" style="{style}">\n'


def _site_nav_html(deck: dict[str, Any], current: int, brand: str) -> str:
    """Persistent site-style top bar: logo, brand, section nav with the
    current section highlighted. Positions are computed server-side so the
    converter sees plain absolutely-placed boxes."""
    parts = [
        '  <div class="nav">',
        (
            f'    <img src="{html.escape(deck["logo"])}" alt="logo" '
            'style="position:absolute;left:40px;top:16px;height:40px;">'
            if deck.get("logo")
            else '    <div class="logo"></div>'
        ),
        f'    <p class="brand">{_esc(brand)}</p>',
    ]
    sections = deck["sections"]
    shown = list(range(min(len(sections), 5)))
    if current not in shown and 0 <= current < len(sections):
        shown[-1] = current
    for slot, section_index in enumerate(shown):
        label = html.escape(sections[section_index]["title"][:6])
        cur = " cur" if section_index == current else ""
        parts.append(
            f'    <div class="nitem{cur}" style="right:{40 + slot * 112}px">'
            f"<p>{label}</p></div>"
        )
    parts.append("  </div>")
    return "\n".join(parts)


def _cover_figures_html(deck: dict[str, Any], zone_width: float, budget: float) -> str:
    """Flow every cover figure into one zone below the title block.

    Each image uses the same {size,pos} grammar as section figures. When the
    stacked heights exceed the zone budget everything shrinks by the same
    deterministic factor (aspect preserved) instead of overflowing the canvas.
    """
    figs = [f for f in (deck.get("cover_images") or []) if not _is_pinned(f)]
    if not figs:
        return ""
    layouts: list[tuple[dict[str, Any], int, int, str, str, float]] = []
    total = 0.0
    for fig in figs:
        w, h, margin, pos = _figure_layout(fig, zone_width)
        cap = 0.0
        if fig.get("alt"):
            cap = (
                estimate_lines(fig["alt"], _FIG_CAPTION_FONT, zone_width)
                * _FIG_CAPTION_FONT
                * 1.4
                + 8.0
            )
        layouts.append((fig, w, h, margin, pos, cap))
        total += h + cap + 10.0
    if total > budget > 0:
        scale = budget / total
        layouts = [
            (f, max(40, int(w * scale)), max(24, int(h * scale)), m, p, c)
            for f, w, h, m, p, c in layouts
        ]
    parts = ['  <div class="cover-figs">']
    for fig, w, h, margin, pos, cap in layouts:
        parts.append(
            f'    <img src="{html.escape(fig["src"])}" alt="" '
            f'style="width:{w}px;height:{h}px;margin:{margin};">'
        )
        if fig.get("alt"):
            align = (
                "left" if pos == "left" else ("right" if pos == "right" else "center")
            )
            parts.append(
                f'    <p class="figcaption" style="text-align:{align};">'
                f"{_esc(fig['alt'])}</p>"
            )
    parts.append("  </div>")
    return "\n".join(parts)


def render_deck(
    markdown: str,
    aspect_ratio: str = "16:9",
    max_bullets: int = DEFAULT_MAX_BULLETS,
    templates_dir: Path | None = None,
    height_scale: float = 1.0,
    theme: str = DEFAULT_THEME,
) -> dict[str, str]:
    """Render a manuscript into {filename: html}. Deterministic for a given input."""
    if aspect_ratio not in CANVAS:
        raise ValueError(f"unsupported aspect_ratio {aspect_ratio!r}")
    if max_bullets < 1:
        raise ValueError("max_bullets must be positive")
    if theme not in THEMES:
        raise ValueError(
            f"unsupported theme {theme!r}; expected one of {', '.join(THEMES)}"
        )
    metrics = THEMES[theme]
    templates_dir = templates_dir or Path(__file__).resolve().parent / "templates"
    theme_dir = templates_dir / theme
    width, height = CANVAS[aspect_ratio]
    deck = require_title(markdown)

    cover_css = _load_css(theme_dir, "cover.css", width, height)
    section_css = _load_css(theme_dir, "section.css", width, height)
    brand = deck["title"][:8] or "PPTSVC"
    if metrics.layout == "site":
        # Figure zone sits below the title block (top 450px) and must keep
        # text captions inside the bottom safe area (48px).
        zone_w, zone_h = float(width - 220), float(height - 450 - 48)
        cover_figs = _cover_figures_html(deck, zone_w, zone_h)
        # Pinned stickers anchor against the page nine-grid — the strip
        # BETWEEN topbar and footbar (chrome is protected by construction).
        pin_under, pin_over = _split_by_z(
            [f for f in (deck.get("cover_images") or []) if _is_pinned(f)]
        )
        zone_args = (
            0.0,
            metrics.topbar_height,
            float(width),
            float(height - metrics.topbar_height - metrics.footbar_height - 16),
        )
        cover_under = _pinned_html(pin_under, *zone_args, under=True)
        cover_pins = _pinned_html(pin_over, *zone_args)
        cover_body = (
            _background_html(deck, width, height)
            + _site_nav_html(deck, -1, brand)
            + ("\n" + cover_under if cover_under else "")
            + "\n"
            + '  <div class="heroBar"></div>\n  <div class="heroPanel"></div>\n'
            + '  <div class="heroDot"></div>\n  <div class="wrap">\n'
            + '    <p class="kicker">PRESENTATION</p>\n'
            + f"    <h1>{_esc(deck['title'])}</h1>\n"
            + f"    <p class=\"sub\">{_esc(deck['lead']) or '&nbsp;'}</p>\n"
            + "  </div>"
            + ("\n" + cover_figs if cover_figs else "")
            + ("\n" + cover_pins if cover_pins else "")
        )
    else:
        cover_body = (
            _background_html(deck, width, height)
            + _logo_html(deck, site=False)
            + '  <div class="band"></div>\n  <div class="wrap">\n'
            '    <p class="kicker">PRESENTATION</p>\n'
            f"    <h1>{_esc(deck['title'])}</h1>\n"
            f"    <p class=\"sub\">{_esc(deck['lead']) or '&nbsp;'}</p>\n"
            "  </div>"
        )
    pages: dict[str, str] = {"slide01.html": _document(cover_css, cover_body)}

    ordinal = 1
    content_width = width - 2 * SIDE_MARGIN
    for index, section in enumerate(deck["sections"], start=1):
        section["blocks"] = _merge_fig_rows(section["blocks"])
        parts = chunk_sections(
            section,
            max_bullets,
            float(content_width),
            float(height),
            height_scale,
            metrics,
        )
        for part, chunk in enumerate(parts):
            ordinal += 1
            pinned: list[dict[str, Any]] = []
            fill_under: list[dict[str, Any]] = []
            local_bg: list[dict[str, Any]] = []
            region_h = float(
                height - BOTTOM_MARGIN_PX - metrics.footbar_height - metrics.content_top
            )
            if metrics.layout == "site":
                body = [
                    _background_html(
                        deck, width, height, (SIDE_MARGIN, metrics.content_top, float(content_width), region_h)
                    ).rstrip("\n"),
                    _site_nav_html(deck, index - 1, brand),
                    f'  <div class="head"><h2>{_esc(chunk["title"])}</h2></div>',
                    '  <div class="rule"></div>',
                    '  <div class="content">',
                ]
            else:
                label = f"{index:02d}" if part == 0 else f"{index:02d}+{part}"
                body = [
                    _background_html(
                        deck, width, height, (SIDE_MARGIN, metrics.content_top, float(content_width), region_h)
                    ).rstrip("\n"),
                    _logo_html(deck, site=False).rstrip("\n"),
                    '  <div class="band"></div>',
                    f'  <div class="num"><p>{label}</p></div>',
                    f'  <div class="head"><h2>{_esc(chunk["title"])}</h2></div>',
                    '  <div class="rule"></div>',
                    '  <div class="content">',
                ]
            for block in chunk["blocks"]:
                text = _esc(block["text"]) if "text" in block else ""
                if block["kind"] == "image":
                    if block.get("alt") == "background":
                        local_bg.append(block)  # this page's backdrop
                        continue
                    if _is_pinned(block):
                        pinned.append(block)  # rendered as an overlay below
                        continue
                    if _is_fill(block):
                        if _pin_z(block) <= 0:
                            fill_under.append(block)  # backdrop below text
                            continue
                        fh = float(block.get("_fill_h", 300.0))
                        dims = _image_size(block["src"])
                        aspect = (dims[1] / dims[0]) if dims and dims[0] else 0.62
                        fw = min(float(content_width), fh / aspect)
                        body.append(
                            f'      <img class="fig" src="{html.escape(block["src"])}" alt="" '
                            f'style="width:{int(fw)}px;height:{int(fw * aspect)}px;margin:0 auto;">'
                        )
                        continue
                    fw, fh, margin, pos = _figure_layout(block, float(content_width))
                    body.append(
                        f'      <img class="fig" src="{html.escape(block["src"])}" alt="" '
                        f'style="width:{fw}px;height:{fh}px;margin:{margin};">'
                    )
                    if block.get("alt"):
                        align = (
                            "left" if pos == "left" else ("right" if pos == "right" else "center")
                        )
                        body.append(
                            f'      <p class="figcaption" style="text-align:{align};">'
                            f"{_esc(block['alt'])}</p>"
                        )
                    continue
                if block["kind"] == "figrow":
                    specs = []
                    row_h = 0.0
                    for item in block["items"]:
                        fw, fh, margin, pos = _figure_layout(item, float(content_width))
                        cap_h = (
                            estimate_lines(item["alt"], _FIG_CAPTION_FONT, fw)
                            * _FIG_CAPTION_FONT
                            * 1.4
                            + 6.0
                            if item.get("alt")
                            else 0.0
                        )
                        specs.append((item, fw, fh, pos))
                        row_h = max(row_h, fh + cap_h)
                    row = [
                        f'      <div class="figrow" style="position:relative;'
                        f'height:{int(row_h)}px;margin:0 0 12px 0;">'
                    ]
                    for item, fw, fh, pos in specs:
                        left = 0 if pos == "left" else int(content_width - fw)
                        row.append(
                            f'        <img class="fig" src="{html.escape(item["src"])}" alt="" '
                            f'style="position:absolute;left:{left}px;top:0;'
                            f'width:{fw}px;height:{fh}px;">'
                        )
                        if item.get("alt"):
                            row.append(
                                f'        <p class="figcaption" style="position:absolute;'
                                f'left:{left}px;top:{int(fh) + 6}px;width:{fw}px;'
                                f'margin:0;text-align:center;">'
                                f"{_esc(item['alt'])}</p>"
                            )
                    row.append("      </div>")
                    body.append("\n".join(row))
                    continue
                if block["kind"] in {"bullet", "number"}:
                    if metrics.layout == "cards":
                        for card_no, item in enumerate(block["items"], start=1):
                            body.append(
                                '      <div class="card">'
                                f'<p class="cnum">{card_no:02d}</p>'
                                f'<p class="ctext">{_esc(item)}</p>'
                                "</div>"
                            )
                        continue
                    tag = "ul" if block["kind"] == "bullet" else "ol"
                    items = "".join(
                        f"<li>{_esc(item)}</li>" for item in block["items"]
                    )
                    body.append(f"      <{tag}>{items}</{tag}>")
                elif block["kind"] == "h3":
                    body.append(f'      <p class="subhead">{text}</p>')
                elif block["kind"] == "lead":
                    body.append(f'      <p class="lead">{text}</p>')
                else:
                    body.append(f"      <p>{text}</p>")
            if local_bg:
                # Section-local backdrop overrides the global one (last wins).
                body[0] = _background_html(
                    {"background": local_bg[-1]},
                    width,
                    height,
                    (SIDE_MARGIN, metrics.content_top, float(content_width), region_h),
                ).rstrip("\n")
            body.append("  </div>")
            if fill_under:
                fz = [
                    f'  <div class="fillzone" style="position:absolute;left:{SIDE_MARGIN}px;'
                    f'top:{int(metrics.content_top)}px;width:{int(content_width)}px;'
                    f'height:{int(region_h)}px;">'
                ]
                for f in fill_under:
                    dims = _image_size(f["src"])
                    aspect = (dims[1] / dims[0]) if dims and dims[0] else 0.62
                    if float(content_width) / aspect <= region_h:
                        fw = float(content_width)
                        fh = fw * aspect
                    else:
                        fh = region_h
                        fw = fh / aspect
                    fz.append(
                        f'    <img src="{html.escape(f["src"])}" alt="" '
                        f'style="position:absolute;left:{int((content_width - fw) / 2)}px;'
                        f'top:{int((region_h - fh) / 2)}px;width:{int(fw)}px;height:{int(fh)}px;">'
                    )
                fz.append("  </div>")
                body.insert(body.index('  <div class="content">'), "\n".join(fz))
            # z layers: under-text zone before .content, over-text after it.
            under_figs, over_figs = _split_by_z(pinned)
            zone_args = (
                0.0,
                metrics.topbar_height,
                float(width),
                float(height - metrics.topbar_height - metrics.footbar_height - 16),
            )
            under_html = _pinned_html(under_figs, *zone_args, under=True)
            over_html = _pinned_html(over_figs, *zone_args)
            if under_html:
                body.insert(body.index('  <div class="content">'), under_html)
            if over_html:
                body.append(over_html)
            pages[f"slide{ordinal:02d}.html"] = _document(section_css, "\n".join(body))
    return pages
