"""Draws the Android launcher icons (all densities) and the white notification icon."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

RES = Path(__file__).parent / "app" / "src" / "main" / "res"
NAVY, BLUE, YELLOW, WHITE = (22, 32, 43), (127, 178, 221), (246, 211, 101), (255, 255, 255)


def logo(size, bg=True, color=None, safe=1.0):
    s = 4
    W = size * s
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bg:
        d.rounded_rectangle([0, 0, W - 1, W - 1], radius=int(W * 0.22), fill=NAVY)
    u = W * safe / 32
    off = (W - 32 * u) / 2
    P = lambda x, y: (off + x * u, off + y * u)
    lw = max(2, int(2.4 * u))
    box, foot = (color or BLUE), (color or YELLOW)
    d.rounded_rectangle([*P(5, 7), *P(18, 25)], radius=int(2 * u), outline=box, width=lw)
    d.line([*P(8, 11.5), *P(15, 11.5)], fill=box, width=max(1, int(1.6 * u)))
    d.line([*P(18, 16), *P(25.5, 16)], fill=foot, width=lw)
    d.line([*P(26, 11), *P(21.5, 16), *P(26, 21)], fill=foot, width=lw, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


for folder, px in {"mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
                   "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192}.items():
    (RES / folder).mkdir(parents=True, exist_ok=True)
    logo(px).save(RES / folder / "ic_launcher.png")

(RES / "drawable-nodpi").mkdir(parents=True, exist_ok=True)
logo(96, bg=False, color=WHITE, safe=1.15).save(RES / "drawable-nodpi" / "ic_notification.png")
print("icons written to", RES, file=sys.stderr)
