# Bricks Sky Segmentation Service

Standalone container, deployed separately from the rest of Bricks-Dispatch
(which runs on Vercel). This service needs ~940MB of Python ML deps
(torch + transformers + model weights), far past Vercel's 250MB serverless
function limit, so it runs on its own host instead.

Self-hosted, free/open-source only: `shi-labs/oneformer_ade20k_swin_tiny`
(MIT license, Hugging Face), PyTorch, `transformers`. No third-party API,
no per-image cost. See `sky_segmentation.py` for the model/license details
and `../../` project memory (`project_bricks_inhouse_photo_enhancement`)
for the full sizing/hosting investigation.

## Deploy target: Oracle Cloud Always Free (Ampere A1, ARM)

1. Provision an Always Free `VM.Standard.A1.Flex` instance (Ubuntu, ARM/aarch64).
   Recommend at least 2 OCPU / 8GB RAM out of the 4 OCPU / 24GB total free
   allocation -- measured peak RAM use of this service during inference is
   ~1.1GB, so this gives comfortable headroom, not a tight fit.
2. Open the instance's security list / firewall to allow inbound traffic on
   whatever port you plan to expose this on (this repo uses 8003 internally;
   put it behind a reverse proxy with TLS if exposing it publicly, or keep
   it on the VM's private/internal network if only Bricks-Dispatch's backend
   needs to reach it).
3. Install Docker on the instance, then from this directory:
   ```
   docker build -t bricks-sky-service .
   docker run -d --name sky-service -p 8003:8003 --restart unless-stopped bricks-sky-service
   ```
4. Confirm it's healthy: `curl http://<instance-ip>:8003/health` should
   return `{"status": "ok"}`.
5. Set `SKY_SERVICE_URL` in Bricks-Dispatch's backend environment to point
   at this instance (not yet added to `backend/.env.example` or wired into
   `post_production.py` -- whether/when sky replacement runs for a given
   job is a routing decision not yet made, see Phase 3 in the memory file
   referenced above).

## API

`POST /replace-sky` -- multipart file upload, returns the sky-replaced
image as JPEG bytes.

`GET /health` -- liveness check.

## Local testing without the Oracle VM

```
pip install -r requirements.txt
uvicorn app:app --reload --port 8003
```
