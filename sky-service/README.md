# Bricks Sky Segmentation Service

Standalone container, deployed separately from the rest of Bricks-Dispatch
(which runs on Vercel). Self-hosted, free/open-source only: no third-party
API, no per-image cost.

## Model

`model/skyseg_int8.onnx` -- a U-2-Net sky segmentation model, dynamically
quantized to INT8 (44MB, down from the original 176MB fp32 export).

- Original model + training: [xiongzhu666/Sky-Segmentation-and-Post-processing](https://github.com/xiongzhu666/Sky-Segmentation-and-Post-processing)
  (MIT license -- verified directly against the repo's LICENSE file, not
  just an inherited tag).
- ONNX (fp32) re-hosting: [JianyuanWang/skyseg](https://huggingface.co/JianyuanWang/skyseg)
  on Hugging Face (MIT license).
- See `model/LICENSE-skyseg-model.txt` for the full license text and
  provenance chain.

This replaced an earlier design built on `shi-labs/oneformer_ade20k_swin_tiny`
(OneFormer/ADE20K, also MIT, also verified). That model was functionally
correct but architecturally too heavy for a card-free host: its Swin
backbone + transformer decoder had an irreducible ~1.1-1.4GB activation
memory floor that neither input downscaling nor ONNX+INT8 quantization
could get under 512MB. U-2-Net is a pure CNN with no transformer decoder,
which is what actually made a card-free fit possible.

## Why two subprocesses per request

`sky_segmentation.py`'s `replace_sky()` shells out to two standalone
scripts (`phase1_segment.py`, `phase2_composite.py`) instead of calling
functions in-process. Measured peak memory (macOS `/usr/bin/time -l`,
isolated per process -- `ru_maxrss`/`time -l`'s "maximum resident set
size" is a lifetime high-water mark, not a live reading, so this can only
be measured by letting a process actually start and exit):

| Approach | Peak RSS |
|---|---|
| Single process, full pipeline, 24MP input | ~1.37 GB |
| + native PIL compositing instead of numpy float32 math | ~866 MB |
| + JPEG draft-mode decode, `del`/`gc.collect()` between phases, same process | ~839 MB |
| Phase 1 alone (segmentation, separate process) | ~503 MB |
| Phase 2 alone (composite, separate process, capped to 2400px output) | ~250 MB |

`del`/`gc.collect()` inside one process does not return memory to the OS,
so the only way to keep the two phases' memory from stacking is to let
each run as its own process and actually exit. That's a real tradeoff:
more subprocess overhead and a second image decode per request, in
exchange for fitting under a 512MB ceiling at all -- something no
single-process design achieved.

Segmentation's ~503MB margin against a 512MB host is thin (~9MB). This is
workable, not bulletproof -- a host with slightly less usable memory than
advertised, or a pathological input, could still OOM. Treat this as
"fits, with real but limited margin," not a guaranteed fit.

## Deploy targets

**Render.com free "Hobby" tier** -- genuinely no card required, 512MB RAM.
This is now viable (see memory numbers above), unlike the earlier
OneFormer design. Spins down after 15 min inactivity (cold start cost on
next request).

**Oracle Cloud Always Free** (Ampere A1, ARM) -- if a card becomes usable
later, this gives 24GB/4 OCPU of headroom and removes the tight-margin
concern entirely. Steps if pursuing this path:
1. Provision a `VM.Standard.A1.Flex` instance (Ubuntu, ARM/aarch64).
2. Open the instance's firewall for whatever port this runs on (8003
   internally; put it behind a reverse proxy with TLS if exposed
   publicly).
3. Install Docker, then: `docker build -t bricks-sky-service .` and
   `docker run -d --name sky-service -p 8003:8003 --restart unless-stopped bricks-sky-service`.
4. Confirm: `curl http://<instance-ip>:8003/health` returns `{"status": "ok"}`.

Either way, set `SKY_SERVICE_URL` in Bricks-Dispatch's backend environment
to point at wherever this ends up running (not yet added to
`backend/.env.example` or wired into `post_production.py` -- whether/when
sky replacement runs for a given job is a routing decision not yet made,
see Phase 3 in the `project_bricks_inhouse_photo_enhancement` memory file).

## API

`POST /replace-sky` -- multipart file upload, returns the sky-replaced
image as JPEG bytes (capped to 2400px on the long edge).

`GET /health` -- liveness check.

## Local testing

```
pip install -r requirements.txt
uvicorn app:app --reload --port 8003
```
