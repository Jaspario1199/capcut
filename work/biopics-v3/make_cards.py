"""Render title-card PNGs for the eight ranked-entry photo slots of the 0805 template.

Each card is the same pixel size as the template's card for that slot, and the
text is laid out inside the slot's crop rectangle, so CapCut's fixed scale and
crop show it exactly where the original title sat.
"""
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
tpl_video = os.path.expandvars(r"%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\0805 Copy Copy Copy\video")
out_dir = sys.argv[2]
# entry order by time = rank 8 .. 1
ENTRIES = [
    ("#8", "YOUR PICK"),
    ("#7", "YOUR PICK"),
    ("#6", "YOUR PICK"),
    ("MONEYBALL", "BILLY BEANE"),
    ("HACKSAW RIDGE", "DESMOND DOSS"),
    ("THE THEORY OF EVERYTHING", "STEPHEN HAWKING"),
    ("THE WOLF OF WALL STREET", "JORDAN BELFORT"),
    ("GOODFELLAS", "HENRY HILL"),
]
YELLOW, WHITE, BLACK = "#F2C230", "#FFFFFF", "#000000"
IMPACT, BOLD = "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf"

def fit(draw, text, font_path, max_w, max_h, start):
    size = start
    while size > 8:
        f = ImageFont.truetype(font_path, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= max_w and b - t <= max_h:
            return f, (r - l, b - t)
        size -= 2
    return ImageFont.truetype(font_path, 8), draw.textbbox((0, 0), text, font=ImageFont.truetype(font_path, 8))[2:]

def wrap(text, max_words=2):
    words = text.split()
    if len(words) <= max_words: return [text]
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]

cards = [s for s in sorted(manifest["media"], key=lambda s: s["target_start_us"])
         if s["material_type"] == "photo" and s["target_duration_us"] < 60_000_000]
assert len(cards) == 8, len(cards)
names = []
for i, (slot, (title, person)) in enumerate(zip(cards, ENTRIES)):
    src = os.path.join(tpl_video, slot["path"].split("/")[-1])
    W, H = Image.open(src).size
    c = slot["crop"]
    x0, y0 = int(c["upper_left_x"] * W), int(c["upper_left_y"] * H)
    x1, y1 = int(c["lower_right_x"] * W), int(c["lower_right_y"] * H)
    im = Image.new("RGB", (W, H), BLACK); d = ImageDraw.Draw(im)
    bw, bh = x1 - x0, y1 - y0
    pad = int(min(bw, bh) * 0.08)
    lines = wrap(title) if bw < bh * 2.2 else [title]
    # title lines take ~68% of the box height, person the rest
    title_h = int((bh - 2 * pad) * 0.68 / len(lines))
    fonts = [fit(d, ln, IMPACT, bw - 2 * pad, title_h, int(bh)) for ln in lines]
    y = y0 + pad
    for ln, (f, (tw, th)) in zip(lines, fonts):
        d.text(((x0 + x1) / 2 - tw / 2, y), ln, font=f, fill=YELLOW)
        y += th + int(pad * 0.6)
    pf, (pw, ph) = fit(d, person, BOLD, bw - 2 * pad, max(10, y1 - pad - y), int(bh * 0.25))
    d.text(((x0 + x1) / 2 - pw / 2, y1 - pad - ph), person, font=pf, fill=WHITE)
    name = f"card{i + 1:02d}_rank{8 - i}_{title.lower().replace(' ', '_').replace('#', 'n')}.png"
    im.save(os.path.join(out_dir, name)); names.append(name)
    print(f"{name}: {W}x{H} box {bw}x{bh} at ({x0},{y0}) lines={lines}")
