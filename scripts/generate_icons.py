#!/usr/bin/env python3
"""
Generate HiImage App Icons - 方案D (极简风)
Purple gradient background, large H + IMAGE subtitle
"""

from PIL import Image, ImageDraw, ImageFont
import os
import math

# ============================================================
# Color palette (from 方案D)
# ============================================================
COLOR_TOP = (127, 119, 221)      # #7F77DD
COLOR_BOTTOM = (83, 74, 183)    # #534AB7
COLOR_H = (238, 237, 254)        # #EEEDFE  (H letter)
COLOR_SUB = (206, 203, 246)     # #CECBF6  (IMAGE subtitle)
COLOR_DOT1 = (238, 237, 254, 128)  # decoration dot (semi-transparent)
COLOR_DOT2 = (175, 169, 236, 128)

OUTPUT_DIR = "D:/Git/HiImage/assets/icons"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def make_gradient_bg(size):
    """Create a rounded-rect purple gradient background."""
    w, h = size, size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw rounded rectangle background with gradient
    # Since PIL doesn't support gradient fills directly, we draw line by line
    radius = int(size * 24 / 120)  # rx=24 for 120px base

    # Create gradient
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(COLOR_TOP[0] + (COLOR_BOTTOM[0] - COLOR_TOP[0]) * ratio)
        g = int(COLOR_TOP[1] + (COLOR_BOTTOM[1] - COLOR_TOP[1]) * ratio)
        b = int(COLOR_TOP[2] + (COLOR_BOTTOM[2] - COLOR_TOP[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    # Apply rounded mask
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    img.putalpha(mask)

    return img


def draw_icon(size):
    """Draw the complete icon at given size."""
    img = make_gradient_bg(size)
    draw = ImageDraw.Draw(img)
    s = size / 120  # scale factor

    # Decoration dots (top-right and bottom-left)
    r_dot1 = max(1, int(8 * s))
    cx1, cy1 = int(90 * s), int(28 * s)
    cx2, cy2 = int(24 * s), int(90 * s)
    # Draw dots as circles on the image (with alpha)
    dot_img = Image.new("RGBA", (w := size, h := size), (0, 0, 0, 0))
    dot_draw = ImageDraw.Draw(dot_img)
    dot_draw.ellipse([cx1-r_dot1, cy1-r_dot1, cx1+r_dot1, cy1+r_dot1],
                      fill=(*COLOR_H[:3], 128))
    dot_draw.ellipse([cx2-r_dot1, cy2-r_dot1, cx2+r_dot1, cy2+r_dot1],
                      fill=(*COLOR_DOT2[:3], 77))
    img = Image.alpha_composite(img, dot_img)

    draw = ImageDraw.Draw(img)

    # --- H Letter ---
    # Try to use a good font, fallback to default
    font_h_size = int(48 * s)
    font_sub_size = int(11 * s)
    letter_spacing = int(2 * s)

    # Try system fonts
    font_paths = [
        "C:/Windows/Fonts/arialbd.ttf",   # Windows Arial Bold
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
    ]

    font_h = None
    font_sub = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font_h = ImageFont.truetype(fp, font_h_size)
                font_sub = ImageFont.truetype(fp, font_sub_size)
                break
            except Exception:
                continue

    if font_h is None:
        font_h = ImageFont.load_default()
        font_sub = font_h

    # Draw H - vertically centered (optical center for H + subtitle)
    # Target: H baseline ~ 65px in 120px icon, IMAGE ~ 90px
    h_text = "H"
    bbox = draw.textbbox((0, 0), h_text, font=font_h)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    hx = (size - tw) / 2
    hy = int(size / 2 - th / 2 - size / 12)
    draw.text((int(hx), int(hy)), h_text, fill=COLOR_H, font=font_h)

    # Draw IMAGE subtitle - closer to H
    sub_text = "IMAGE"
    bbox2 = draw.textbbox((0, 0), sub_text, font=font_sub)
    sw = bbox2[2] - bbox2[0]
    sx = (size - sw) / 2
    sy = int(size / 2 + th / 2 + int(11 * s) - size / 12 + size * 10 / 120)
    draw.text((int(sx), int(sy)), sub_text, fill=COLOR_SUB, font=font_sub,
              spacing=letter_spacing)

    return img


def generate_all():
    """Generate all icon sizes for Windows and macOS."""

    # --- PNG icons for macOS (.iconset) and general use ---
    # macOS iconset sizes
    macos_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }

    iconset_dir = os.path.join(OUTPUT_DIR, "HiImage.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    print("Generating macOS iconset PNGs...")
    for fname, sz in macos_sizes.items():
        img = draw_icon(sz)
        out_path = os.path.join(iconset_dir, fname)
        img.save(out_path, "PNG")
        print(f"  {fname} ({sz}x{sz})")

    # --- Windows ICO (multi-size) ---
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_images = []
    print("\nGenerating Windows ICO...")
    for sz in ico_sizes:
        img = draw_icon(sz)
        if sz == 256:
            img = img.resize((256, 256), Image.LANCZOS)
        ico_images.append(img)

    ico_path = os.path.join(OUTPUT_DIR, "icon.ico")
    # Save with multiple sizes
    ico_images[0].save(
        ico_path,
        format="ICO",
        sizes=[(sz, sz) for sz in ico_sizes],
        append_images=ico_images[1:]
    )
    print(f"  Saved: {ico_path}")

    # --- Large standalone PNGs ---
    for sz in [64, 128, 256, 512, 1024]:
        img = draw_icon(sz)
        out = os.path.join(OUTPUT_DIR, f"icon-{sz}.png")
        img.save(out, "PNG")
        print(f"  icon-{sz}.png")

    # --- macOS ICNS (Pillow supports ICNS format) ---
    icns_path = os.path.join(OUTPUT_DIR, "icon.icns")
    # ICNS requires specific sizes
    icns_sizes = [16, 32, 128, 256, 512, 1024]
    icns_images = [(sz, draw_icon(sz)) for sz in icns_sizes]
    # Save ICNS (Pillow handles the format)
    try:
        icns_images[0][1].save(icns_path, format="ICNS", sizes=[(sz, sz) for sz in icns_sizes])
        print(f"  Saved: {icns_path}")
    except Exception as e:
        print(f"  ICNS save failed (Pillow may not support ICNS on Windows): {e}")
        print(f"  → Use macOS: iconutil -c icns \"{iconset_dir}\"")

    print(f"\nIcons generated in: {OUTPUT_DIR}")
    print(f"  - icon.ico (Windows)")
    print(f"  - HiImage.iconset/ (macOS, convert with: iconutil -c icns HiImage.iconset)")
    print(f"  - icon-*.png (standalone)")

    return OUTPUT_DIR


if __name__ == "__main__":
    generate_all()
