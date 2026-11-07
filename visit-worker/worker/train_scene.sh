#!/usr/bin/env bash
# Train one scene with gsplat simple_trainer (MCMC), sampling GPU memory, and write timing JSON.
# usage: train_scene.sh <scene_dir> <out_dir> [iters] [cap_max]
set -euo pipefail
SCENE=$1; OUT=$2; ITERS=${3:-15000}; CAP=${4:-1000000}
mkdir -p "$OUT"
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -lms 1000 > "$OUT/vram_samples.txt" &
SMI=$!
trap 'kill $SMI 2>/dev/null || true' EXIT
t0=$(date +%s.%N)
cd /root/gsplat/examples
python simple_trainer.py mcmc --data-dir "$SCENE" --data-factor 1 --result-dir "$OUT" \
  --max-steps "$ITERS" --eval-steps "$ITERS" --save-steps "$ITERS" --save-ply --ply-steps "$ITERS" \
  --disable-viewer --disable-video --test-every 8 --strategy.cap-max "$CAP" > "$OUT/train.log" 2>&1
t1=$(date +%s.%N)
kill $SMI 2>/dev/null || true
PEAK=$(sort -n "$OUT/vram_samples.txt" | tail -1)
python - "$OUT" "$t0" "$t1" "$PEAK" "$ITERS" "$CAP" <<'PY'
import json, sys, glob, os
out, t0, t1, peak, iters, cap = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
r = {"train_wall_s": round(t1 - t0, 1), "peak_vram_mib_nvidia_smi": int(peak or 0), "iters": iters, "cap_max": cap}
for f in sorted(glob.glob(os.path.join(out, "stats", "*.json"))):
    r[os.path.basename(f)] = json.load(open(f))
json.dump(r, open(os.path.join(out, "timing.json"), "w"), indent=2)
print(json.dumps(r))
PY
