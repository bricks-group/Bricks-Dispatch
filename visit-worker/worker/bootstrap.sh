#!/usr/bin/env bash
# Prepare a stock runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04 pod for training.
# (The Dockerfile bakes the same steps into an image; this is the no-build path used by the pilot.)
set -euo pipefail
VW=${VW:-/root/vw}
GSPLAT_REF=v1.5.3
FUSED_SSIM_REF=328dc9836f513d00c4b5bc38fe30478b4435cbb5
t0=$(date +%s)
python -c "import torch;assert torch.cuda.is_available();print('torch',torch.__version__,torch.cuda.get_device_name(0))"
pip install -q --no-cache-dir --no-deps -r "$VW/requirements-train.txt"
TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.9}" MAX_JOBS=8 pip install -q --no-cache-dir --no-build-isolation --no-deps \
  "git+https://github.com/rahul-goel/fused-ssim@${FUSED_SSIM_REF}"
if [ ! -d /root/gsplat ]; then
  git clone -q --depth 1 --branch "$GSPLAT_REF" https://github.com/nerfstudio-project/gsplat.git /root/gsplat
fi
python -c "import gsplat, fused_ssim; print('gsplat', gsplat.__version__)"
if [ "${WITH_PACK:-0}" = 1 ] && ! command -v splat-transform >/dev/null; then
  (curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null && apt-get install -y -qq nodejs >/dev/null) || true
  npm i -g @playcanvas/splat-transform@3.10.1 >/dev/null
fi
if [ "${WITH_SFM:-0}" = 1 ] && [ ! -x /opt/sfm/bin/python ]; then
  python -m venv /opt/sfm && /opt/sfm/bin/pip install -q --no-cache-dir "pycolmap==4.2.1" numpy pillow
  command -v ffmpeg >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg unzip >/dev/null) || true
fi
echo "{\"bootstrap_s\": $(( $(date +%s) - t0 ))}" > /root/bootstrap_timing.json
echo BOOTSTRAP_OK
