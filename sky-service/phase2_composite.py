"""
Phase 2 of the sky-replacement pipeline: upsample the mask, generate a
procedural replacement sky, feather the seam, ambient-match the lighting,
and composite -- run as a standalone subprocess for the same reason as
phase1_segment.py (see sky_segmentation.py). Decodes the source photo via
libjpeg's draft mode capped to OUTPUT_CAP, since real-estate listing photos
don't need full sensor resolution and decoding at full resolution just to
downscale it afterward wastes most of the memory budget on pixels that get
thrown away.
"""

import sys

import numpy as np
from PIL import Image, ImageFilter

OUTPUT_CAP = 2400  # long-edge px, well above typical MLS/web display size


def _load_capped(image_path: str, cap: int) -> Image.Image:
    image = Image.open(image_path)
    image.draft("RGB", (cap, cap))
    image = image.convert("RGB")
    w, h = image.size
    if max(w, h) > cap:
        scale = cap / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)))
    return image


def _generate_procedural_sky(width: int, height: int, seed: int | None = None) -> Image.Image:
    rng = np.random.default_rng(seed)
    top_color = np.array([90, 150, 225], dtype=np.float32)  # RGB: clear blue sky
    horizon_color = np.array([200, 220, 235], dtype=np.float32)

    t = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    gradient = top_color[None, None, :] * (1 - t) + horizon_color[None, None, :] * t
    gradient = np.broadcast_to(gradient, (height, width, 3)).copy()

    noise = rng.normal(0, 1, size=(height // 8, width // 8)).astype(np.float32)
    noise_img = Image.fromarray(((noise - noise.min()) / (noise.max() - noise.min()) * 255).astype(np.uint8))
    noise_img = noise_img.resize((width, height), resample=Image.BICUBIC)
    noise_img = noise_img.filter(ImageFilter.GaussianBlur(radius=max(width, height) / 60))
    clouds = np.asarray(noise_img, dtype=np.float32) / 255.0
    cloud_strength = (clouds[..., None] - 0.5) * 40  # subtle brightening/darkening, not solid clouds

    sky = np.clip(gradient + cloud_strength, 0, 255).astype(np.uint8)
    return Image.fromarray(sky)


def _match_ambient_light(sky_img: Image.Image, scene_img: Image.Image, non_sky_alpha: np.ndarray) -> Image.Image:
    scene_arr = np.asarray(scene_img, dtype=np.float32)
    weights = (1 - non_sky_alpha)[..., None]
    if weights.sum() < 1e-3:
        return sky_img  # no visible ground/scene to calibrate against
    scene_brightness = (scene_arr * weights).sum() / (weights.sum() * 3)
    sky_arr = np.asarray(sky_img, dtype=np.float32)
    sky_brightness = sky_arr.mean()
    if sky_brightness < 1e-3:
        return sky_img
    factor = np.clip(scene_brightness / sky_brightness, 0.7, 1.3)
    matched = np.clip(sky_arr * factor, 0, 255).astype(np.uint8)
    return Image.fromarray(matched)


def composite(image_path: str, mask_path: str, feather_radius: float, seed: int | None = None) -> Image.Image:
    image = _load_capped(image_path, OUTPUT_CAP)
    w, h = image.size

    mask_small = Image.open(mask_path)
    mask_full = mask_small.resize((w, h))
    feathered = mask_full.filter(ImageFilter.GaussianBlur(radius=feather_radius))

    sky = _generate_procedural_sky(w, h, seed=seed)
    alpha = np.asarray(feathered, dtype=np.float32) / 255.0
    sky = _match_ambient_light(sky, image, alpha)

    return Image.composite(sky, image, feathered)


if __name__ == "__main__":
    image_path, mask_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    feather_radius = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    result = composite(image_path, mask_path, feather_radius)
    result.save(output_path, quality=90)
