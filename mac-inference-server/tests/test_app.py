"""
Tests for the Mac inference server's HTTP contract and auth -- deliberately
does NOT load the real LaMa model or torch/simple-lama-inpainting (those are
only ever imported lazily, inside `_load_model()`, so importing this test
module never triggers a real model download or requires those packages to
be installed). `_load_model` is monkeypatched to install a fake callable
model before the app's lifespan runs, mirroring how a real Apple Silicon Mac
would have a model loaded at startup -- what's under test here is the
request/response contract and the auth gate, not LaMa's own output quality
(that needs a real Mac, see this PR's report).

Run from this directory: `pip install -r requirements.txt fastapi[all] pytest`
then `pytest tests/ -q` (or reuse Dispatch's backend virtualenv, which
already has fastapi/Pillow/httpx installed).
"""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as mac_app


class _FakeLamaModel:
    """Stands in for SimpleLama: returns a solid-color image the same size
    as the input, so tests can assert on the response contract without any
    real inpainting happening."""

    def __call__(self, image: Image.Image, mask: Image.Image) -> Image.Image:
        return Image.new("RGB", image.size, color=(10, 20, 30))


class _BrokenLamaModel:
    def __call__(self, image, mask):
        raise RuntimeError("simulated inference crash")


def _make_jpeg_bytes(size=(64, 48), color=(200, 50, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_mask_bytes(size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    # A simple white square on black -- doesn't matter what shape for these
    # contract tests, only that it decodes as a valid single-channel mask.
    mask = Image.new("L", size, color=0)
    mask.paste(255, (10, 10, 30, 30))
    mask.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client_with_model(monkeypatch):
    monkeypatch.setattr(mac_app, "_load_model", lambda: _install_fake_model())
    monkeypatch.setenv(mac_app.MAC_INFERENCE_KEY_ENV, "test-secret-key")
    with TestClient(mac_app.app) as c:
        yield c


def _install_fake_model():
    mac_app._model = _FakeLamaModel()
    mac_app._model_load_error = None
    mac_app._device_used = "cpu"


@pytest.fixture
def client_model_unavailable(monkeypatch):
    def _fail_load():
        mac_app._model = None
        mac_app._model_load_error = "RuntimeError: no internet, could not fetch checkpoint"
        mac_app._device_used = None

    monkeypatch.setattr(mac_app, "_load_model", _fail_load)
    monkeypatch.setenv(mac_app.MAC_INFERENCE_KEY_ENV, "test-secret-key")
    with TestClient(mac_app.app) as c:
        yield c


class TestHealth:
    def test_health_reports_model_loaded(self, client_with_model):
        resp = client_with_model.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["model_loaded"] is True
        assert body["model_load_error"] is None

    def test_health_reports_model_unavailable(self, client_model_unavailable):
        resp = client_model_unavailable.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "model_unavailable"
        assert body["model_loaded"] is False
        assert "checkpoint" in body["model_load_error"]

    def test_health_requires_no_auth(self, client_with_model):
        """Reachability checks (Dispatch's failover logic) must work without
        the shared secret -- only /inpaint carries real photo data."""
        resp = client_with_model.get("/health")
        assert resp.status_code == 200


class TestInpaintAuth:
    def test_missing_key_is_401(self, client_with_model):
        resp = client_with_model.post(
            "/inpaint",
            files={
                "image": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(), "image/png"),
            },
        )
        assert resp.status_code == 401

    def test_wrong_key_is_401(self, client_with_model):
        resp = client_with_model.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "not-the-right-key"},
            files={
                "image": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(), "image/png"),
            },
        )
        assert resp.status_code == 401

    def test_unconfigured_server_secret_is_500(self, client_with_model, monkeypatch):
        monkeypatch.delenv(mac_app.MAC_INFERENCE_KEY_ENV, raising=False)
        resp = client_with_model.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "anything"},
            files={
                "image": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(), "image/png"),
            },
        )
        assert resp.status_code == 500


class TestInpaintContract:
    def test_valid_request_returns_jpeg_same_size_as_input(self, client_with_model):
        size = (64, 48)
        resp = client_with_model.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "test-secret-key"},
            files={
                "image": ("photo.jpg", _make_jpeg_bytes(size), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(size), "image/png"),
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        result_img = Image.open(io.BytesIO(resp.content))
        assert result_img.size == size

    def test_mismatched_mask_size_is_resized_not_rejected(self, client_with_model):
        """A mask drawn against a differently-sized preview is a realistic
        real-world case for a future editor UI -- must not 422."""
        resp = client_with_model.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "test-secret-key"},
            files={
                "image": ("photo.jpg", _make_jpeg_bytes((64, 48)), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes((32, 24)), "image/png"),
            },
        )
        assert resp.status_code == 200

    def test_unreadable_image_is_422(self, client_with_model):
        resp = client_with_model.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "test-secret-key"},
            files={
                "image": ("photo.jpg", b"not an image", "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(), "image/png"),
            },
        )
        assert resp.status_code == 422

    def test_model_unavailable_is_503(self, client_model_unavailable):
        resp = client_model_unavailable.post(
            "/inpaint",
            headers={"X-Mac-Inference-Key": "test-secret-key"},
            files={
                "image": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg"),
                "mask": ("mask.png", _make_mask_bytes(), "image/png"),
            },
        )
        assert resp.status_code == 503

    def test_inference_crash_is_500_not_unhandled(self, monkeypatch):
        def _install_broken_model():
            mac_app._model = _BrokenLamaModel()
            mac_app._model_load_error = None
            mac_app._device_used = "cpu"

        monkeypatch.setattr(mac_app, "_load_model", _install_broken_model)
        monkeypatch.setenv(mac_app.MAC_INFERENCE_KEY_ENV, "test-secret-key")
        with TestClient(mac_app.app) as c:
            resp = c.post(
                "/inpaint",
                headers={"X-Mac-Inference-Key": "test-secret-key"},
                files={
                    "image": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg"),
                    "mask": ("mask.png", _make_mask_bytes(), "image/png"),
                },
            )
        assert resp.status_code == 500
