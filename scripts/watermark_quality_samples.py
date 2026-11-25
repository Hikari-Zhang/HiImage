"""Versioned ground truth for synthetic scenes and photographs with controlled overlays."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

VERSION = 2
SIZE = (512, 384)
SCENES = ("gradient", "brick", "foliage", "sign", "portrait")
WATERMARKS = ("translucent", "repeated", "tilted", "icon", "opaque", "clean")
CORPORA = ("synthetic", "photo", "all")
PHOTO_DIRECTORY = Path(__file__).with_name("fixtures") / "watermark_quality"
PHOTO_REVISION = "e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae"
PHOTO_SOURCE = f"https://raw.githubusercontent.com/scikit-image/scikit-image/{PHOTO_REVISION}/skimage/data"


@dataclass(frozen=True)
class Photo:
    id: str
    filename: str
    credit: str
    license: str
    sha256: str

    @property
    def url(self):
        return f"{PHOTO_SOURCE}/{self.filename}"


PHOTOS = (
    Photo("chelsea", "chelsea.png", "Stefan van der Walt", "CC0 1.0",
          "596aa1e7cb875eb79f437e310381d26b338a81c2da23439704a73c4651e8c4bb"),
    Photo("clock", "clock_motion.png", "Stefan van der Walt", "Public domain dedication",
          "f029226b28b642e80113d86622e9b215ee067a0966feaf5e60604a1e05733955"),
    Photo("coffee", "coffee.png", "Rachel Michetti (courtesy of Pikolo Espresso Bar)", "CC0 1.0",
          "cc02f8ca188b167c775a7101b5d767d1e71792cf762c33d6fa15a4599b5a8de7"),
)


@dataclass(frozen=True)
class Sample:
    id: str
    original: Image.Image
    input: Image.Image
    mask: Image.Image
    regions: tuple[tuple[int, int, int, int], ...] = ()
    photo: Photo | None = None


def background(scene):
    width, height = SIZE
    y, x = np.indices((height, width))
    if scene == "gradient":
        rgb = (60 + x // 5, 90 + y // 4, 160 + y // 8)
    elif scene == "brick":
        mortar = (y % 24 < 2) | ((x + (y // 24 % 2) * 28) % 56 < 2)
        texture = (x * 17 + y * 11) % 21
        rgb = tuple(np.where(mortar, 175, base + texture) for base in (130, 65, 45))
    elif scene == "foliage":
        texture = np.random.default_rng(811).integers(0, 80, size=(height, width))
        texture += ((x // 8 + y // 11) % 3) * 24
        rgb = (20 + texture // 2, 55 + texture, 25 + texture // 3)
    else:
        rgb = (70 + x // 9, 94 + y // 9, 110 + y // 8)
    image = Image.fromarray(np.stack(rgb, axis=-1).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    if scene == "sign":
        draw.rectangle((80, 126, 432, 254), fill="#234166", outline="white", width=4)
        lettering = Image.new("L", (100, 14))
        ImageDraw.Draw(lettering).text((1, 1), "STATION  42", fill=255)
        image.paste("white", (115, 166), lettering.resize((300, 42), Image.Resampling.NEAREST))
    elif scene == "portrait":
        draw.ellipse((135, 55, 377, 325), fill="#d99a76", outline="#502f29", width=5)
        for eye_x in (190, 280):
            draw.ellipse((eye_x, 151, eye_x + 40, 176), fill="white")
            draw.ellipse((eye_x + 14, 153, eye_x + 28, 174), fill="#422e27")
        draw.line([(254, 169), (241, 212), (265, 213)], fill="#995942", width=4)
        draw.arc((218, 208, 294, 258), 15, 165, fill="#823d3a", width=5)
    return image


def watermark(kind):
    mask = Image.new("L", SIZE)
    if kind == "clean":
        return mask, ()
    if kind == "icon":
        draw = ImageDraw.Draw(mask)
        draw.ellipse((219, 153, 293, 227), outline=145, width=6)
        draw.line([(234, 191), (248, 205), (277, 175)], fill=145, width=7)
        return mask, (mask.getbbox(),)
    label = Image.new("L", (72, 14))
    ImageDraw.Draw(label).text((2, 1), "HIIMAGE TEST", fill=255 if kind == "opaque" else 110)
    label = label.resize((216, 42), Image.Resampling.NEAREST)
    if kind == "tilted":
        label = label.rotate(26, expand=True, resample=Image.Resampling.BICUBIC)
    centers = ((256, 110), (256, 192), (256, 274)) if kind == "repeated" else ((256, 192),)
    left, top, right, bottom = label.getbbox()
    regions = []
    for x, y in centers:
        origin_x, origin_y = x - label.width // 2, y - label.height // 2
        mask.paste(label, (origin_x, origin_y))
        regions.append((origin_x + left, origin_y + top, origin_x + right, origin_y + bottom))
    return mask, tuple(regions)


def stamp_samples(scene, original, photo=None):
    for kind in WATERMARKS:
        alpha, regions = watermark(kind)
        stamped = original.copy()
        stamped.paste((248, 242, 224), (0, 0), alpha)
        mask = alpha.point(lambda value: 255 if value else 0)
        yield Sample(f"{scene}-{kind}", original, stamped, mask, regions, photo)


def samples(corpus="synthetic"):
    if corpus in ("synthetic", "all"):
        for scene in SCENES:
            yield from stamp_samples(scene, background(scene))
    if corpus in ("photo", "all"):
        for photo in PHOTOS:
            with Image.open(PHOTO_DIRECTORY / photo.filename) as source:
                original = ImageOps.fit(source.convert("RGB"), SIZE, method=Image.Resampling.LANCZOS)
            yield from stamp_samples(f"photo-{photo.id}", original, photo)
