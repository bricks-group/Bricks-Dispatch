# Bricks Mac Inference Server

A small, self-hosted inpainting API you run on your own Apple Silicon Mac.
It does **mask-guided object removal**: you give it a photo and a mask
marking what to erase, it fills that region in to match the surrounding
scene, and hands back the finished photo. Dispatch's backend
(`backend/app/services/object_removal_service.py`) calls this server first
for object-removal requests, and falls back to Gemini if this Mac is
asleep, offline, or the call fails.

This is **not part of the `backend/` FastAPI app** and is never deployed to
Vercel — it's a separate program that runs on different hardware (your
Mac), reachable over the internet via a tunnel.

**What this does NOT do:** decide *what* to remove from a photo. It only
runs the mask it's given — auto-detecting clutter/cars/cords on its own is
a separate, not-yet-built capability. See the root `CLAUDE.md`'s Object
Removal section.

## Model

[LaMa](https://github.com/advimman/lama) (Large Mask Inpainting), via the
[`simple-lama-inpainting`](https://pypi.org/project/simple-lama-inpainting/)
pip package. LaMa is a small (~27M parameter, ~200MB checkpoint)
Fourier-convolution network built specifically for resolution-robust mask
inpainting — not a diffusion model — which is why it's realistic to run on
an 8GB Mac: modest memory footprint, no GPU strictly required, and fast
enough on CPU alone for one-photo-at-a-time real estate work. (Researched
2026-09-12 against IOPaint/lama-cleaner and a couple of other current LaMa
wrappers — `simple-lama-inpainting` was the best-maintained plain pip
package that does exactly "image + mask in, filled image out" with no extra
UI/server bundled in, which is what this FastAPI wrapper needed.)

The first time the server starts, `simple-lama-inpainting` downloads the
~200MB `big-lama.pt` checkpoint to a local cache directory — this needs a
working internet connection once, not on every request.

## 1. Install

Requirements: **macOS on Apple Silicon**, Python 3.10–3.12, ~1GB free disk
for the model + dependencies.

```bash
cd mac-inference-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`pip install` pulls in PyTorch. On macOS/arm64, the standard PyPI `torch`
wheel already includes Apple MPS (Metal GPU) support — you don't need a
special index URL or a separate CUDA-style package like you would on Linux.

## 2. Configure

Set a shared secret — this server will be exposed to the public internet,
so this is real auth, not just an obscure URL. Generate a long random
value and keep it somewhere safe (you'll paste it into Vercel too):

```bash
export MAC_INFERENCE_KEY="$(openssl rand -hex 32)"
```

Optional tuning (defaults shown):

```bash
export MAC_INFERENCE_MAX_DIMENSION=1536   # long-edge cap before inference; lower = less RAM/time
export MAC_INFERENCE_DEVICE=auto          # auto | mps | cpu -- auto prefers Apple GPU, falls back to CPU
```

If you're on the 8GB machine mentioned in the project's build notes, leave
`MAC_INFERENCE_MAX_DIMENSION` at 1536 or lower. Raise it once you've
upgraded RAM.

## 3. Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8811
```

The first request that needs the model (or server startup, since the model
loads eagerly at boot) will download the LaMa checkpoint if it isn't
cached yet — give it a minute the first time.

Verify locally:

```bash
curl http://localhost:8811/health
# {"status":"ok","model_loaded":true,"model_load_error":null,"device":"mps"}
```

If `device` comes back `"cpu"` even though you're on Apple Silicon, that's
fine — LaMa's Fourier-convolution layers aren't all implemented for Metal
yet, and the server sets `PYTORCH_ENABLE_MPS_FALLBACK=1` so unsupported ops
silently fall back to CPU per-operation instead of crashing. You still get
partial GPU acceleration where it's supported.

**Keep the Mac awake.** If it sleeps, it stops answering requests (Dispatch
will just fail over to Gemini, but you presumably want the Mac path
working). Either disable sleep while plugged in (System Settings → Energy),
or run `caffeinate -dims &` alongside the server.

## 4. Expose it to the internet (Cloudflare Tunnel)

Cloudflare Tunnel is free, needs no port forwarding or static IP, and is
the recommended option here.

```bash
brew install cloudflared
```

### Quick option (fastest — good for getting started)

```bash
cloudflared tunnel --url http://localhost:8811
```

This prints a random `https://<something>.trycloudflare.com` URL — that's
your `MAC_INFERENCE_URL`. No Cloudflare account needed. **Trade-off:** this
URL changes every time you restart `cloudflared`, so you'd need to update
Dispatch's Vercel env var each time.

### Persistent option (a stable URL — recommended once this is more than a one-off test)

Requires a domain already using Cloudflare DNS (e.g. `brxs.ca`, if/when it's
on Cloudflare — otherwise use the Quick option above).

```bash
cloudflared tunnel login                                  # opens a browser, pick your domain
cloudflared tunnel create bricks-mac-inference             # note the tunnel UUID it prints
cloudflared tunnel route dns bricks-mac-inference mac-inference.yourdomain.com
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <the-uuid-from-tunnel-create>
credentials-file: /Users/<you>/.cloudflared/<the-uuid>.json
ingress:
  - hostname: mac-inference.yourdomain.com
    service: http://localhost:8811
  - service: http_status:404
```

Run it:

```bash
cloudflared tunnel run bricks-mac-inference
```

Now `https://mac-inference.yourdomain.com` is your stable `MAC_INFERENCE_URL`
— it stays the same across restarts, so you only configure Vercel once.
(To keep this running in the background long-term, look into `cloudflared
service install`, which sets it up as a launchd service.)

## 5. Tell Dispatch's Vercel project about it

In the `bricks-go-api` Vercel project's environment variables, set:

```
MAC_INFERENCE_URL=https://<your-tunnel-url>
MAC_INFERENCE_KEY=<the same secret you generated in step 2>
```

Redeploy (or it'll pick it up on the next deploy). Dispatch's
`object_removal_service.py` will now try this Mac first for object-removal
requests, falling back to Gemini if it's unreachable or a call fails.

## API reference

### `GET /health`

No auth required (used for a cheap reachability check before Dispatch
attempts a real upload).

```json
{"status": "ok", "model_loaded": true, "model_load_error": null, "device": "mps"}
```

### `POST /inpaint`

Auth: header `X-Mac-Inference-Key: <MAC_INFERENCE_KEY>`.

Request: `multipart/form-data` with two files:
- `image` — the source photo (JPEG/PNG)
- `mask` — a single-channel image, same pixel dimensions as `image` (if not,
  the server resizes it to match) — **white (255) = remove/fill this
  region, black (0) = leave unchanged**.

Response: `200 image/jpeg` — the filled-in photo, same dimensions as the
original upload, as raw JPEG bytes (not JSON/base64).

Errors: `401` bad/missing key, `422` unreadable image or mask, `500` server
misconfigured (no `MAC_INFERENCE_KEY` set) or inference crashed, `503`
model failed to load (e.g. no internet on first boot to fetch the
checkpoint) — check `/health` for the reason.

```bash
curl -X POST http://localhost:8811/inpaint \
  -H "X-Mac-Inference-Key: $MAC_INFERENCE_KEY" \
  -F "image=@photo.jpg" \
  -F "mask=@mask.png" \
  -o result.jpg
```

## Testing

```bash
pip install pytest
pytest tests/ -q
```

These tests mock the LaMa model entirely (no real inference, no model
download) — they check the HTTP contract (multipart request shape, auth,
error codes) only. They don't and can't verify real inpainting quality on
actual Apple Silicon hardware; that needs a real run on your Mac.
