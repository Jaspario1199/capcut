"""Title cards: a PNG with styled text, for the template's photo slots.

Mobile-made templates often carry each entry's title as a baked image rather
than a text material. Those slots take an image, so we render one. Pillow only;
no CapCut JSON is touched. The card is transparent by default so the template's
own placement (scale, position, crop) keeps working.
"""

from __future__ import annotations

from pathlib import Path

FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Impact.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


class CardError(Exception):
    pass


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise CardError("pip install 'capcut-recreate[cards]' (Pillow) for title cards") from e
    return Image, ImageDraw, ImageFont


def find_font(explicit: str | None = None) -> str:
    if explicit:
        if not Path(explicit).exists():
            raise CardError(f"font not found: {explicit}")
        return explicit
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise CardError("no bold font found; pass --font <path to .ttf>")


def _parse_color(s: str) -> tuple[int, int, int, int]:
    named = {"white": (255, 255, 255, 255), "black": (0, 0, 0, 255), "transparent": (0, 0, 0, 0),
             "red": (220, 30, 30, 255), "yellow": (255, 210, 0, 255), "gold": (255, 190, 40, 255)}
    if s.lower() in named:
        return named[s.lower()]
    h = s.lstrip("#")
    if len(h) in (6, 8):
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
        return (r, g, b, a)
    raise CardError(f"bad colour {s!r}; use a name or #rrggbb[aa]")


def image_size(path: str | Path) -> tuple[int, int]:
    Image, _, _ = _pil()
    with Image.open(path) as im:
        return im.size


def render_title(text: str, out: Path, size: tuple[int, int] = (720, 960), *, font: str | None = None,
                 fill: str = "white", stroke: str = "black", stroke_width: int | None = None,
                 background: str = "transparent", max_width_frac: float = 0.9, max_height_frac: float = 0.9,
                 line_spacing: float = 1.1, uppercase: bool = False) -> Path:
    """Render `text` centred on a size[0] x size[1] canvas. `|` or newline breaks lines.

    The font size is the largest at which every line fits max_width_frac of the
    width and the block fits max_height_frac of the height.
    """
    Image, ImageDraw, ImageFont = _pil()
    w, h = size
    if w <= 0 or h <= 0:
        raise CardError("size must be positive")
    lines = [ln.strip() for ln in text.replace("|", "\n").split("\n")]
    lines = [ln for ln in lines if ln] or [""]
    if uppercase:
        lines = [ln.upper() for ln in lines]
    font_path = find_font(font)

    def block(pt: int):
        f = ImageFont.truetype(font_path, pt)
        widths, heights = [], []
        for ln in lines:
            l, t, r, b = f.getbbox(ln or " ")
            widths.append(r - l)
            heights.append(b - t)
        lh = int(pt * line_spacing)
        return f, max(widths), lh * (len(lines) - 1) + max(heights), lh

    lo, hi = 8, max(8, h)
    while lo < hi:  # largest point size that fits
        mid = (lo + hi + 1) // 2
        _, bw, bh, _ = block(mid)
        if bw <= w * max_width_frac and bh <= h * max_height_frac:
            lo = mid
        else:
            hi = mid - 1
    f, bw, bh, lh = block(lo)
    sw = stroke_width if stroke_width is not None else max(2, lo // 14)

    im = Image.new("RGBA", (w, h), _parse_color(background))
    d = ImageDraw.Draw(im)
    y = (h - bh) // 2
    for ln in lines:
        l, t, r, b = f.getbbox(ln or " ")
        x = (w - (r - l)) // 2 - l
        d.text((x, y - t), ln, font=f, fill=_parse_color(fill), stroke_width=sw, stroke_fill=_parse_color(stroke))
        y += lh
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "PNG")
    return out
