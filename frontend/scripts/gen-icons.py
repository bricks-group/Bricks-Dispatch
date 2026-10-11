#!/usr/bin/env python3
"""Generate Bricks app icons/splash/store graphics from the Bricks brick mark.

The mark is the three-brick glyph from assets/bricks-mark.svg / favicon.svg
(24x24 viewBox): top brick (3,4,11x6.4), bottom-left (3,11.6,6x8.4),
bottom-right (10.2,11.6,10.8x8.4), brick red #D2542B.

usage: gen_icons.py <out_dir> <bg_hex> <wordmark_label> [feature_fg_hex]
"""
import sys, os
from PIL import Image, ImageDraw, ImageFont

BRICK = (0xD2, 0x54, 0x2B, 255)
RECTS = [(3, 4, 11, 6.4, 0.82), (3, 11.6, 6, 8.4, 1.0), (10.2, 11.6, 10.8, 8.4, 1.0)]
SS = 4  # supersample

def hexrgb(h):
    h = h.lstrip('#'); return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (255,)

KEEP_OPACITY = True

def mark(size, color=BRICK, keep_opacity=None):
    if keep_opacity is None: keep_opacity = KEEP_OPACITY
    """Render the mark filling a size x size box (mark bbox 3..21 x 4..20 is centred)."""
    S = size * SS
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # mark bbox: x 3..21 (18), y 4..20 (16). scale to fit width.
    k = S / 18.0
    oy = (S - 16 * k) / 2
    for x, y, w, h, op in RECTS:
        a = int(255 * (op if keep_opacity else 1))
        c = color[:3] + (a,)
        x0 = (x - 3) * k; y0 = (y - 4) * k + oy
        d.rounded_rectangle([x0, y0, x0 + w * k, y0 + h * k], radius=0.8 * k, fill=c)
    return im.resize((size, size), Image.LANCZOS)

def place(canvas, m, frac):
    W, H = canvas.size
    s = int(min(W, H) * frac)
    mm = m(s)
    canvas.alpha_composite(mm, ((W - s) // 2, (H - s) // 2))
    return canvas

def main():
    global KEEP_OPACITY
    out, bg = sys.argv[1], hexrgb(sys.argv[2])
    # On dark backgrounds the 82%-opacity top brick reads muddy; use the solid GO favicon form.
    KEEP_OPACITY = (0.299*bg[0] + 0.587*bg[1] + 0.114*bg[2]) / 255 > 0.5
    label = sys.argv[3] if len(sys.argv) > 3 else 'bricks'
    fg_text = hexrgb(sys.argv[4]) if len(sys.argv) > 4 else (0xF4, 0xF1, 0xEB, 255)
    os.makedirs(out, exist_ok=True)
    # 1. App Store / iOS icon: 1024 opaque, no alpha (Apple rejects transparency).
    ic = place(Image.new('RGBA', (1024, 1024), bg), mark, 0.62)
    ic.convert('RGB').save(f'{out}/icon.png')
    # 2. Android adaptive foreground: transparent, mark inside the 66% safe circle.
    place(Image.new('RGBA', (1024, 1024), (0, 0, 0, 0)), mark, 0.46).save(f'{out}/adaptive-icon.png')
    # 3. Android 13+ themed (monochrome) icon.
    place(Image.new('RGBA', (1024, 1024), (0, 0, 0, 0)), lambda s: mark(s, (255, 255, 255, 255), False), 0.46).save(f'{out}/adaptive-icon-monochrome.png')
    # 4. Android notification small icon: white silhouette on transparent, 96px.
    place(Image.new('RGBA', (96, 96), (0, 0, 0, 0)), lambda s: mark(s, (255, 255, 255, 255), False), 0.80).save(f'{out}/notification-icon.png')
    # 5. Splash image (expo-splash-screen scales it to imageWidth).
    place(Image.new('RGBA', (1024, 1024), (0, 0, 0, 0)), mark, 0.9).save(f'{out}/splash-icon.png')
    # 6. Web favicon.
    place(Image.new('RGBA', (64, 64), (0, 0, 0, 0)), mark, 0.9).save(f'{out}/favicon.png')
    # 7. Store graphics: Play 512 hi-res icon, Play 1024x500 feature graphic.
    store = os.path.join(out, 'store'); os.makedirs(store, exist_ok=True)
    ic.resize((512, 512), Image.LANCZOS).convert('RGB').save(f'{store}/play-icon-512.png')
    ic.convert('RGB').save(f'{store}/app-store-icon-1024.png')
    fg = Image.new('RGBA', (1024, 500), bg)
    m = mark(220); fg.alpha_composite(m, (110, 140))
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/sand-box/google/Outfit/Outfit-VariableFont_wght.ttf', 112)
        try: font.set_variation_by_name('Medium')
        except Exception: pass
    except Exception:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(fg)
    d.text((380, 250), label, font=font, fill=fg_text, anchor='lm')
    fg.convert('RGB').save(f'{store}/play-feature-graphic-1024x500.png')
    print('wrote', out)

if __name__ == '__main__':
    main()
