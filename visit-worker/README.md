# visit-worker: Bricks Virtual Visit GPU worker (pilot, not wired to production)

Phone capture (video or images) → frames → SfM (GLOMAP via pycolmap) → gsplat 1.5.3 training (MCMC) → `.ply` →
`.sog` (PlayCanvas SOG) + `.spz` (v3) → automatic QC (`pass` | `hold`) → upload to pre-signed URLs → pod terminates itself.

**Policy baked in:** Runpod **Secure Cloud only** (`runpod_api.create_pod` refuses anything else; client interiors stay off
Community hosts), on-demand (non-interruptible), `volumeInGb: 0`, no network volumes, every pod is terminated (DELETE),
never just stopped.

## Layout
| Path | What |
|---|---|
| `Dockerfile` | Production image: runpod/pytorch 2.1 (py3.10, CUDA 11.8) + gsplat 1.5.3 wheel + fused-ssim + pycolmap 4.2.1 (separate venv) + ffmpeg + splat-transform 3.10.1 |
| `requirements-train.txt` | Locked deps for gsplat `examples/simple_trainer.py` (install with `--no-deps`) |
| `worker/bootstrap.sh` | Same install on a stock Runpod PyTorch pod (no image build); used by the pilot |
| `worker/frames.py` | ffmpeg frames at 3 fps, drops blurriest 15 % and near-duplicates, optional GO room-tap split |
| `worker/sfm.py` | SIFT + exhaustive/sequential matching + GLOMAP (fallback incremental COLMAP); writes `sparse/0` at training resolution + `sfm_stats.json` |
| `worker/train_scene.sh` | gsplat MCMC training, every 8th frame held out, nvidia-smi VRAM sampling, `timing.json` |
| `worker/pack.sh` | `.sog` (CPU k-means, `-g cpu`) + `.spz` v3; decimates once if `.sog` > budget (15 MB) |
| `worker/qc.py` | Automatic QC gates → `qc.json` with verdict `pass`/`hold` and reasons |
| `worker/entry_job.sh` | In-pod job: download capture URL → pipeline → PUT outputs → PUT `result.json` → self-terminate (EXIT trap + MAX_RUNTIME timer) |
| `launcher/runpod_api.py` | Stdlib Runpod REST/GraphQL client (balance, create Secure pod, terminate with verify, sweeper) |
| `launcher/launch_job.py` | Capture URL + pre-signed output URLs in → pod → output URLs + QC verdict out; terminates in `finally` + detached watchdog |
| `launcher/pilot_ssh.py` | Pilot runner: one pod, scenes over SSH, sequential training, copy back, terminate (try/finally + atexit + signals + watchdog + spend cap) |

## QC gates (worker/qc.py) — any failed or missing gate → `hold` (Dispatch returns 422 `visit_pipeline_qc_held`)
| Gate | Threshold | Why |
|---|---|---|
| `reg_ratio` | ≥ 0.85 | frames that registered in SfM; below this the room has holes |
| `mean_reproj_px` | ≤ 1.0 px | SfM-resolution reprojection error; higher = bad poses = blur |
| `sfm_models` | == 1 | one connected model per floor (rooms must share coordinates) |
| `holdout_psnr` | ≥ 25 dB | every 8th frame is never trained on; gsplat Mip-NeRF 360 reference ≈ 29–32 dB indoors |
| `holdout_ssim` | ≥ 0.85 | same holdout frames |
| `outside_bbox_ratio` | ≤ 3 % | visible splats (opacity > 0.1) outside the 2–98 % SfM-point/camera bbox padded 10 % |
| `big_faint_ratio` | ≤ 5 % | splats with max scale > 2 % of bbox diagonal and opacity < 0.1 (fog/floaters) |
| `sog_mb` | ≤ 15 MB | per-room streaming budget (design doc §4) |
| `render_luma` / `render_coverage` | 0.06–0.94 / ≥ 0.60 | headless viewer screenshot isn't blank/black/white |

## Run the pilot again
```bash
# SfM on the box CPU first (no GPU time): python worker/sfm.py --sfm_images <low-res> --train_images <train-res> --out <scene>
bash -lc 'python3 launcher/pilot_ssh.py --scene-root <dir with scenes> --scenes room kitchen counter \
          --out <runs dir> --iters 15000 --max-minutes 80 --max-usd 3'
```

## Not done yet (deliberately)
- No production trigger: nothing enqueues `launch_job.py`. Wire later from the `visit_jobs` queue → `/api/capture/visit/deliver`.
- Image not pushed to a registry (needs a Bricks registry login; agents must not mint credentials). The pilot uses the stock
  image + `bootstrap.sh`, which costs ~2–4 GPU-minutes per pod for installs.
- Multi-room split, base splat, entry_pose and floor plan (design doc §3 steps 3, 4b, 5, 6).
