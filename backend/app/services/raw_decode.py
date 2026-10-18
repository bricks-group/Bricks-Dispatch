"""Camera RAW → working JPEG for the stills pipeline.

Vendors upload camera RAW brackets (Bricks Standard v1.1: vendors capture,
Bricks edits). The RAW stays the master in storage; editing, QC, previews,
share pages and zips work from a JPEG derivative made here.

Decode order:
1. rawpy (LibRaw) embedded preview when it is big enough (long edge
   >= RAW_EMBEDDED_MIN_LONG_EDGE). That is the camera's own rendering at the
   shot exposure, the same pixels vendors used to upload as camera JPEGs, so
   bracket spacing and the in-house edit tuning are unchanged.
2. rawpy full demosaic (camera white balance, no auto-brightening so a
   bracket keeps its EV spacing) for cameras with small previews (Sony ARW,
   Panasonic RW2, some DNGs).
3. Without rawpy: the largest decodable JPEG embedded in the file.

Anything that cannot be decoded raises RawDecodeError so the caller holds the
still for a human instead of failing the job.

EXIF that the edit needs (ExposureBiasValue, capture time, camera) is read
from the RAW header and written into the derivative; orientation is baked in.
"""
from __future__ import annotations

import io
import logging
import os
import struct
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Upload accepts these (sign/complete + web picker parity in bracket-require.ts).
CAMERA_RAW_EXTS: Tuple[str, ...] = (
    ".cr2", ".cr3", ".crw", ".dng", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".raf", ".orf", ".rw2", ".pef", ".ptx", ".srw", ".x3f", ".raw", ".rwl",
    ".3fr", ".fff", ".iiq", ".mos", ".erf", ".kdc", ".mrw",
)

# Canonical Content-Type per extension. Browsers usually send "" or
# application/octet-stream for RAW; storage gets a stable type either way.
RAW_MIME_BY_EXT: Dict[str, str] = {
    ".cr2": "image/x-canon-cr2",
    ".cr3": "image/x-canon-cr3",
    ".crw": "image/x-canon-crw",
    ".dng": "image/x-adobe-dng",
    ".nef": "image/x-nikon-nef",
    ".nrw": "image/x-nikon-nrw",
    ".arw": "image/x-sony-arw",
    ".srf": "image/x-sony-srf",
    ".sr2": "image/x-sony-sr2",
    ".raf": "image/x-fuji-raf",
    ".orf": "image/x-olympus-orf",
    ".rw2": "image/x-panasonic-rw2",
    ".pef": "image/x-pentax-pef",
    ".ptx": "image/x-pentax-pef",
    ".srw": "image/x-samsung-srw",
    ".x3f": "image/x-sigma-x3f",
    ".rwl": "image/x-leica-rwl",
    ".3fr": "image/x-hasselblad-3fr",
    ".fff": "image/x-hasselblad-fff",
    ".iiq": "image/x-phaseone-iiq",
    ".mos": "image/x-leaf-mos",
    ".erf": "image/x-epson-erf",
    ".kdc": "image/x-kodak-kdc",
    ".mrw": "image/x-minolta-mrw",
    ".raw": "image/x-panasonic-raw",
}

RAW_EMBEDDED_MIN_LONG_EDGE = int(os.getenv("RAW_EMBEDDED_MIN_LONG_EDGE", "2400"))
RAW_WORK_MAX_LONG_EDGE = int(os.getenv("RAW_WORK_MAX_LONG_EDGE", "6000"))
RAW_WORK_JPEG_QUALITY = int(os.getenv("RAW_WORK_JPEG_QUALITY", "90"))
# LibRaw without auto-bright renders ~0.7–1 EV darker than the camera JPEG.
# One fixed lift for every frame keeps bracket spacing intact.
RAW_DECODE_BRIGHT = float(os.getenv("RAW_DECODE_BRIGHT", "1.7"))

RAW_REVIEW_QC_NOTE = (
    "Camera RAW could not be decoded — held for human review, not delivered. "
    "Open the RAW on a desktop or ask the vendor for the JPEG of this frame."
)


class RawDecodeError(Exception):
    """RAW bytes could not be turned into a working image."""


@dataclass
class RawDecodeResult:
    image: Image.Image
    method: str
    exif: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _lower(v: Any) -> str:
    return str(v or "").strip().lower()


def ext_of(name: Any) -> str:
    base = _lower(name).replace("\\", "/").split("/")[-1].split("?", 1)[0]
    return base[base.rfind("."):] if "." in base else ""


def is_camera_raw_name(name: Any) -> bool:
    return ext_of(name) in CAMERA_RAW_EXTS


def raw_mime_for(name: Any, fallback: str = "application/octet-stream") -> str:
    return RAW_MIME_BY_EXT.get(ext_of(name), fallback)


_CAMERA_MAKES = (
    b"canon", b"nikon", b"sony", b"fujifilm", b"olympus", b"om digital",
    b"panasonic", b"pentax", b"ricoh", b"samsung", b"leica", b"hasselblad",
    b"sigma", b"phase one", b"apple", b"google", b"dji", b"xiaomi", b"huawei",
)


# TIFF compression codes only RAW writers use (Nikon, Sony, Samsung, Pentax,
# lossless JPEG at sensor depth). Plain TIFF exports use 1/5/7(8-bit)/8/32773.
_RAW_TIFF_COMPRESSION = frozenset({32767, 32769, 32770, 34713, 34892, 65535})


def sniff_camera_raw(data: Optional[bytes]) -> bool:
    """True when bytes are a camera RAW (not a JPEG/PNG/plain TIFF export).

    Cheap header checks only. TIFF-based RAW (NEF/ARW/PEF/SRW/DNG) is told
    apart from a normal TIFF by DNGVersion, or a camera Make plus a reduced-
    resolution IFD0 / SubIFDs (how every RAW stores its preview).
    """
    if not data or len(data) < 16:
        return False
    head = data[:16]
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return False
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return True
    if head[4:8] == b"ftyp" and data[8:12] in (b"crx ", b"CRX "):
        return True
    if head[:4] in (b"IIRO", b"IIRS", b"MMOR", b"IIU\x00"):
        return True
    if head[:4] == b"II*\x00" and head[8:10] == b"CR":
        return True
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        try:
            tags = _TiffReader(data, 0).ifd_tags(_TiffReader(data, 0).first_ifd())
        except Exception:
            return False
        if 0xC612 in tags:  # DNGVersion
            return True
        make = tags.get(0x010F)
        make_b = (make if isinstance(make, bytes) else str(make or "").encode()).lower()
        if any(m in make_b for m in _CAMERA_MAKES):
            if 0x014A in tags or tags.get(0x00FE) == 1:
                return True
            bits = tags.get(0x0102)
            bits = bits[0] if isinstance(bits, tuple) and bits else bits
            if tags.get(0x0103) in _RAW_TIFF_COMPRESSION or bits in (10, 12, 14):
                return True
    return False


# ---------------------------------------------------------------------------
# Minimal TIFF / EXIF reader (IFD0 + Exif IFD) for RAW headers
# ---------------------------------------------------------------------------

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


class _TiffReader:
    def __init__(self, data: bytes, base: int):
        self.d = data
        self.b = base
        order = data[base:base + 2]
        if order == b"II":
            self.e = "<"
        elif order == b"MM":
            self.e = ">"
        else:
            raise ValueError("not TIFF")

    def u16(self, off: int) -> int:
        return struct.unpack_from(self.e + "H", self.d, self.b + off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from(self.e + "I", self.d, self.b + off)[0]

    def first_ifd(self) -> int:
        return self.u32(4)

    def ifd_tags(self, ifd: int, limit: int = 400) -> Dict[int, Any]:
        out: Dict[int, Any] = {}
        if ifd <= 0 or self.b + ifd + 2 > len(self.d):
            return out
        n = self.u16(ifd)
        for i in range(min(n, limit)):
            e = ifd + 2 + i * 12
            if self.b + e + 12 > len(self.d):
                break
            tag = self.u16(e)
            typ = self.u16(e + 2)
            cnt = self.u32(e + 4)
            size = _TYPE_SIZE.get(typ, 1) * cnt
            voff = e + 8 if size <= 4 else self.u32(e + 8)
            if self.b + voff + min(size, 64) > len(self.d) and size <= 4096:
                continue
            try:
                out[tag] = self._value(typ, cnt, voff)
            except Exception:
                continue
        return out

    def _value(self, typ: int, cnt: int, voff: int) -> Any:
        p = self.b + voff
        if typ == 2:
            return self.d[p:p + cnt].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
        if typ in (1, 7):
            return self.d[p:p + min(cnt, 4096)]
        if typ == 3:
            vals = struct.unpack_from(self.e + "H" * min(cnt, 64), self.d, p)
        elif typ == 4 or typ == 13:
            vals = struct.unpack_from(self.e + "I" * min(cnt, 64), self.d, p)
        elif typ == 9:
            vals = struct.unpack_from(self.e + "i" * min(cnt, 64), self.d, p)
        elif typ in (5, 10):
            fmt = "I" if typ == 5 else "i"
            vals = []
            for k in range(min(cnt, 16)):
                num, den = struct.unpack_from(self.e + fmt * 2, self.d, p + 8 * k)
                vals.append(Fraction(num, den) if den else None)
            vals = tuple(vals)
        else:
            return None
        return vals[0] if cnt == 1 else tuple(vals)


def _exif_from_tiff(data: bytes, base: int, *, exif_only: bool = False) -> Dict[str, Any]:
    r = _TiffReader(data, base)
    out: Dict[str, Any] = {}
    ifd0 = r.ifd_tags(r.first_ifd())
    if exif_only:
        exif = ifd0
    else:
        for tag, key in ((0x010F, "make"), (0x0110, "model"), (0x0112, "orientation")):
            if ifd0.get(tag) is not None:
                out[key] = ifd0[tag]
        exif = r.ifd_tags(int(ifd0[0x8769])) if ifd0.get(0x8769) else {}
    for tag, key in (
        (0x9204, "exposure_bias"),
        (0x829A, "exposure_time"),
        (0x829D, "fnumber"),
        (0x8827, "iso"),
        (0x9003, "datetime_original"),
        (0x9291, "subsec_original"),
    ):
        v = exif.get(tag)
        if v is not None:
            out[key] = v
    return out


def _cr3_exif(data: bytes) -> Dict[str, Any]:
    """Canon CR3: CMT1 (IFD0) and CMT2 (Exif IFD) boxes inside moov/uuid."""
    out: Dict[str, Any] = {}
    window = data[: 4 * 1024 * 1024]
    for box, exif_only in ((b"CMT1", False), (b"CMT2", True)):
        i = window.find(box)
        if i < 4:
            continue
        try:
            out.update(_exif_from_tiff(data, i + 4, exif_only=exif_only))
        except Exception:
            continue
    return out


def read_raw_exif(data: bytes) -> Dict[str, Any]:
    """Best-effort EXIF from a RAW header. Never raises."""
    try:
        if data[4:8] == b"ftyp":
            return _cr3_exif(data)
        if data.startswith(b"FUJIFILMCCD-RAW"):
            off, ln = struct.unpack_from(">II", data, 84)
            return _exif_from_jpeg(data[off:off + ln])
        if data[:2] in (b"II", b"MM"):
            return _exif_from_tiff(data, 0)
    except Exception as e:
        logger.debug("raw_decode: exif read failed: %s", e)
    return {}


def _exif_from_jpeg(jpeg: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with Image.open(io.BytesIO(jpeg)) as im:
            ex = im.getexif()
            for tag, key in ((0x010F, "make"), (0x0110, "model"), (0x0112, "orientation")):
                if ex.get(tag) is not None:
                    out[key] = ex.get(tag)
            sub = ex.get_ifd(0x8769) or {}
            for tag, key in (
                (0x9204, "exposure_bias"), (0x829A, "exposure_time"), (0x829D, "fnumber"),
                (0x8827, "iso"), (0x9003, "datetime_original"), (0x9291, "subsec_original"),
            ):
                if sub.get(tag) is not None:
                    out[key] = sub.get(tag)
    except Exception:
        pass
    return out


def exposure_bias(exif: Dict[str, Any]) -> Optional[float]:
    v = exif.get("exposure_bias")
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _rational(v: Any):
    from PIL.TiffImagePlugin import IFDRational

    f = v if isinstance(v, Fraction) else Fraction(float(v)).limit_denominator(100000)
    return IFDRational(f.numerator, f.denominator)


def _write_exif(img: Image.Image, exif: Dict[str, Any]) -> bytes:
    ex = Image.Exif()
    if exif.get("make"):
        ex[0x010F] = str(exif["make"])[:64]
    if exif.get("model"):
        ex[0x0110] = str(exif["model"])[:64]
    ex[0x0112] = 1  # orientation already baked into pixels
    ex[0x0131] = "Bricks RAW working copy"
    sub = ex.get_ifd(0x8769)
    bias = exposure_bias(exif)
    if bias is not None:
        sub[0x9204] = _rational(Fraction(bias).limit_denominator(100))
    for key, tag in (("exposure_time", 0x829A), ("fnumber", 0x829D)):
        v = exif.get(key)
        try:
            if v is not None and float(v) > 0:
                sub[tag] = _rational(v)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    iso = exif.get("iso")
    if isinstance(iso, tuple) and iso:
        iso = iso[0]
    if isinstance(iso, int) and 0 < iso < 65536:
        sub[0x8827] = iso
    for key, tag in (("datetime_original", 0x9003), ("subsec_original", 0x9291)):
        if exif.get(key):
            sub[tag] = str(exif[key])[:32]
    try:
        return ex.tobytes()
    except Exception as e:
        logger.warning("raw_decode: exif write failed: %s", e)
        try:
            ex.pop(0x8769, None)
            return ex.tobytes()
        except Exception:
            return b""


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def _rawpy():
    try:
        import rawpy  # type: ignore

        return rawpy
    except Exception:
        return None


def rawpy_available() -> bool:
    return _rawpy() is not None


_FLIP_TO_TRANSPOSE = {
    3: Image.Transpose.ROTATE_180,
    5: Image.Transpose.ROTATE_90,   # 90° CCW
    6: Image.Transpose.ROTATE_270,  # 90° CW
}


def _orient_preview(img: Image.Image, flip: int) -> Image.Image:
    """Embedded previews are stored sensor-side up. Honour their own EXIF
    orientation when present, else LibRaw's flip for the RAW."""
    try:
        own = int(img.getexif().get(0x0112) or 1)
    except Exception:
        own = 1
    if own != 1:
        out = ImageOps.exif_transpose(img)
        return out if out is not None else img
    t = _FLIP_TO_TRANSPOSE.get(int(flip or 0))
    return img.transpose(t) if t is not None else img


def _scan_embedded_jpegs(data: bytes) -> Optional[Image.Image]:
    """Largest baseline/progressive JPEG embedded in the file (no rawpy)."""
    best = None
    best_px = 0
    pos = 0
    tries = 0
    while tries < 64:
        i = data.find(b"\xff\xd8\xff", pos)
        if i < 0:
            break
        pos = i + 3
        tries += 1
        try:
            im = Image.open(io.BytesIO(data[i:]))
            if im.format != "JPEG":
                continue
            w, h = im.size
            if w * h <= best_px:
                continue
            im.load()
            best, best_px = im, w * h
        except Exception:
            continue
    return best


def _fit(img: Image.Image, max_long_edge: int) -> Image.Image:
    w, h = img.size
    le = max(w, h)
    if max_long_edge and le > max_long_edge:
        s = max_long_edge / float(le)
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    return img


def decode_raw(
    data: bytes,
    *,
    max_long_edge: int = RAW_WORK_MAX_LONG_EDGE,
    min_embedded_long_edge: int = RAW_EMBEDDED_MIN_LONG_EDGE,
) -> RawDecodeResult:
    """RAW bytes → oriented sRGB PIL image + header EXIF. Raises RawDecodeError."""
    if not data:
        raise RawDecodeError("empty RAW")
    exif = read_raw_exif(data)
    rp = _rawpy()
    errors = []
    if rp is not None:
        try:
            with rp.imread(io.BytesIO(data)) as raw:
                flip = int(getattr(raw.sizes, "flip", 0) or 0)
                try:
                    thumb = raw.extract_thumb()
                except Exception as e:  # no / unsupported preview
                    thumb = None
                    errors.append(f"thumb:{type(e).__name__}")
                if thumb is not None and thumb.format == rp.ThumbFormat.JPEG:
                    try:
                        pv = Image.open(io.BytesIO(thumb.data))
                        pv.load()
                        if max(pv.size) >= min_embedded_long_edge:
                            if not exif.get("exposure_bias"):
                                exif = {**_exif_from_jpeg(thumb.data), **exif}
                            img = _orient_preview(pv, flip)
                            return RawDecodeResult(
                                _fit(_to_srgb(img), max_long_edge), "embedded_preview", exif
                            )
                    except Exception as e:
                        errors.append(f"preview:{type(e).__name__}")
                w = int(getattr(raw.sizes, "width", 0) or 0)
                half = bool(w and max_long_edge and w // 2 >= int(max_long_edge * 0.5) and w // 2 >= 3000)
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    bright=RAW_DECODE_BRIGHT,
                    output_bps=8,
                    half_size=half,
                )
                img = Image.fromarray(rgb, "RGB")
                return RawDecodeResult(_fit(img, max_long_edge), "libraw_demosaic", exif)
        except RawDecodeError:
            raise
        except Exception as e:
            errors.append(f"rawpy:{type(e).__name__}:{str(e)[:80]}")
    pv = _scan_embedded_jpegs(data)
    if pv is not None and max(pv.size) >= 1000:
        img = ImageOps.exif_transpose(pv) or pv
        return RawDecodeResult(_fit(_to_srgb(img), max_long_edge), "embedded_scan", exif)
    raise RawDecodeError("; ".join(errors) or "no decodable image in RAW")


def _to_srgb(img: Image.Image) -> Image.Image:
    try:
        from .image_io import to_srgb_rgb

        return to_srgb_rgb(img)
    except Exception:
        return img.convert("RGB")


def raw_to_working_jpeg(
    data: bytes,
    *,
    max_long_edge: int = RAW_WORK_MAX_LONG_EDGE,
    quality: int = RAW_WORK_JPEG_QUALITY,
) -> Tuple[bytes, Dict[str, Any]]:
    """RAW → full-quality sRGB JPEG with exposure EXIF. Raises RawDecodeError."""
    res = decode_raw(data, max_long_edge=max_long_edge)
    buf = io.BytesIO()
    kw: Dict[str, Any] = {"format": "JPEG", "quality": quality, "optimize": True}
    exif_bytes = _write_exif(res.image, res.exif)
    if exif_bytes:
        kw["exif"] = exif_bytes
    res.image.convert("RGB").save(buf, **kw)
    meta = {
        "method": res.method,
        "width": res.image.size[0],
        "height": res.image.size[1],
        "exposure_bias": exposure_bias(res.exif),
        "model": res.exif.get("model"),
    }
    return buf.getvalue(), meta


def working_jpeg_path(raw_storage_path: str) -> str:
    """Deterministic derivative key next to the RAW master."""
    p = str(raw_storage_path or "")
    head, _, base = p.rpartition("/")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{head}/work_{stem}.jpg" if head else f"work_{stem}.jpg"


def preview_jpeg_path(raw_storage_path: str) -> str:
    p = str(raw_storage_path or "")
    head, _, base = p.rpartition("/")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{head}/thumb_{stem}.jpg" if head else f"thumb_{stem}.jpg"
