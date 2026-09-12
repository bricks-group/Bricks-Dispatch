"""
Sanity tests for the in-house CV enhancement functions (Phase 1, 2026-09-11).

CV output is not pixel-exact/deterministic across environments (this sandbox
doesn't even have cv2 installed -- enhance_image() falls back to the PIL-only
path, same as it would in production if cv2 were ever unavailable), so these
tests check "ran without crashing, expected direction of change, correct
image dimensions/format preserved" rather than exact pixel values.
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.services.photo_enhance_service import (
    enhance_image,
    enhance_sky,
    sky_enhance_enabled,
    window_pull,
    window_pull_enabled,
)


def _flat_image(size=(240, 180), rgb=(120, 110, 100)) -> Image.Image:
    return Image.new("RGB", size, rgb)


def _image_with_blown_window(size=(300, 200)) -> Image.Image:
    """A mid-grey room with a fully blown-out rectangular 'window' region."""
    width, height = size
    arr = np.full((height, width, 3), 100, dtype=np.uint8)
    # Roughly 20% of the frame, well above the 1% detection floor.
    arr[40:150, 90:240] = 255
    return Image.fromarray(arr, "RGB")


def _image_with_sky(size=(300, 300)) -> Image.Image:
    """A ground plane plus a blue sky band across the top ~40% of the frame."""
    width, height = size
    arr = np.full((height, width, 3), 100, dtype=np.uint8)
    sky_band = int(height * 0.4)
    arr[:sky_band, :, 0] = 100
    arr[:sky_band, :, 1] = 150
    arr[:sky_band, :, 2] = 230
    return Image.fromarray(arr, "RGB")


def _to_jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


class TestWindowPull:
    def test_reduces_brightness_in_blown_region_without_crashing(self):
        img = _image_with_blown_window()
        region_box = (100, 50, 230, 140)  # inside the blown rectangle
        before = np.array(img.crop(region_box), dtype=np.float32).mean()

        result = window_pull(img)

        after = np.array(result.crop(region_box), dtype=np.float32).mean()
        assert after < before, "expected the blown window region to get measurably darker"
        assert result.size == img.size
        assert result.mode == "RGB"

    def test_noop_on_plain_image_with_no_blown_region(self):
        img = _flat_image()
        result = window_pull(img)

        assert result.size == img.size
        assert result.mode == "RGB"
        # No significant blown-highlight area -- should be an effective no-op.
        before = np.array(img, dtype=np.float32)
        after = np.array(result, dtype=np.float32)
        assert np.allclose(before, after, atol=1.0)

    def test_disabled_flag_helper_reads_env(self, monkeypatch):
        monkeypatch.delenv("INHOUSE_WINDOW_PULL_ENABLED", raising=False)
        assert window_pull_enabled() is True
        monkeypatch.setenv("INHOUSE_WINDOW_PULL_ENABLED", "false")
        assert window_pull_enabled() is False


class TestSkyEnhance:
    def test_runs_without_crashing_on_sky_image(self):
        img = _image_with_sky()
        result = enhance_sky(img)

        assert result.size == img.size
        assert result.mode == "RGB"
        # Saturation/contrast boost should change pixel values somewhere in
        # the (upper) sky band without blowing up the whole image.
        before = np.array(img, dtype=np.float32)
        after = np.array(result, dtype=np.float32)
        assert not np.array_equal(before, after)

    def test_noop_on_interior_shot_with_no_sky(self):
        img = _flat_image(rgb=(90, 80, 70))
        result = enhance_sky(img)

        assert result.size == img.size
        assert result.mode == "RGB"
        before = np.array(img, dtype=np.float32)
        after = np.array(result, dtype=np.float32)
        assert np.allclose(before, after, atol=1.0)

    def test_disabled_flag_helper_reads_env(self, monkeypatch):
        monkeypatch.delenv("INHOUSE_SKY_ENHANCE_ENABLED", raising=False)
        assert sky_enhance_enabled() is True
        monkeypatch.setenv("INHOUSE_SKY_ENHANCE_ENABLED", "false")
        assert sky_enhance_enabled() is False


class TestEnhanceImagePipeline:
    def test_plain_image_round_trips_without_raising(self):
        img = _flat_image()
        out_bytes = enhance_image(_to_jpeg_bytes(img))

        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.size == img.size
        assert out_img.mode == "RGB"

    def test_window_and_sky_image_round_trips_without_raising(self):
        img = _image_with_sky()
        out_bytes = enhance_image(_to_jpeg_bytes(img))

        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.size == img.size
        assert out_img.mode == "RGB"

    def test_a_failing_window_pull_does_not_break_the_pipeline(self, monkeypatch):
        """Per spec: a failure in either additive refinement must fall back to
        the image without that specific enhancement and keep going, not raise."""
        import app.services.photo_enhance_service as svc

        monkeypatch.setattr(svc, "window_pull", lambda img: (_ for _ in ()).throw(RuntimeError("boom")))
        img = _flat_image()

        out_bytes = svc.enhance_image(_to_jpeg_bytes(img))

        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.size == img.size

    def test_a_failing_sky_enhance_does_not_break_the_pipeline(self, monkeypatch):
        import app.services.photo_enhance_service as svc

        monkeypatch.setattr(svc, "enhance_sky", lambda img: (_ for _ in ()).throw(RuntimeError("boom")))
        img = _flat_image()

        out_bytes = svc.enhance_image(_to_jpeg_bytes(img))

        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.size == img.size
