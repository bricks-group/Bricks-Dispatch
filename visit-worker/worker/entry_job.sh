#!/usr/bin/env bash
# In-pod job entry (custom image, or stock image + bootstrap.sh). Runs once, uploads outputs to pre-signed PUT URLs,
# writes result.json, then terminates its own pod. Env:
#   JOB_ID, CAPTURE_URL (signed GET: .mp4/.mov video or .zip of images), JOB_SPEC (JSON: outputs{name:{put}}, result{put},
#   rooms[] optional GO tap timestamps), ITERS (15000), CAP_MAX (1000000), MAX_RUNTIME_S (5400)
#   RUNPOD_POD_ID / RUNPOD_API_KEY are injected by Runpod into every pod (pod-scoped key) and used only to self-terminate.
set -uo pipefail
VW=${VW:-/opt/vw}; J=/root/job; mkdir -p $J/out
ITERS=${ITERS:-15000}; CAP_MAX=${CAP_MAX:-1000000}; MAX_RUNTIME_S=${MAX_RUNTIME_S:-5400}
self_terminate() {
  echo "self-terminate $RUNPOD_POD_ID"
  runpodctl remove pod "$RUNPOD_POD_ID" >/dev/null 2>&1 || \
  curl -s -X DELETE "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" -H "Authorization: Bearer $RUNPOD_API_KEY" >/dev/null || true
}
( sleep "$MAX_RUNTIME_S"; echo "MAX_RUNTIME hit"; self_terminate ) &
STATUS=failed; STAGE=start; T0=$(date +%s)
declare -A TIMES
stage() { STAGE=$1; TIMES[$1]=$(date +%s); echo "== $1"; }
put() { # put <local file> <output name>
  local url; url=$(python3 -c "import json,os,sys;print(json.loads(os.environ['JOB_SPEC'])['outputs'].get(sys.argv[1],{}).get('put',''))" "$2")
  [ -n "$url" ] && curl -sf -X PUT -T "$1" -H "Content-Type: application/octet-stream" "$url" >/dev/null && echo "uploaded $2"
}
finish() {
  python3 - "$STATUS" "$STAGE" "$T0" > $J/result.json <<'PY'
import json, sys, os, glob, time
status, stage, t0 = sys.argv[1], sys.argv[2], int(sys.argv[3])
r = {"job_id": os.environ.get("JOB_ID"), "status": status, "failed_stage": None if status == "ok" else stage,
     "wall_s": int(time.time()) - t0, "pod_id": os.environ.get("RUNPOD_POD_ID")}
for f in ["/root/job/out/qc.json", "/root/job/out/timing.json", "/root/job/out/pack.json", "/root/job/scene/sfm_stats.json"]:
    if os.path.exists(f): r[os.path.basename(f)[:-5]] = json.load(open(f))
print(json.dumps(r))
PY
  url=$(python3 -c "import json,os;print(json.loads(os.environ['JOB_SPEC'])['result']['put'])")
  curl -sf -X PUT -T $J/result.json -H "Content-Type: application/json" "$url" >/dev/null || true
  self_terminate
}
trap finish EXIT
set -e
[ -d /root/gsplat ] || bash $VW/bootstrap.sh
stage download
curl -sfL "$CAPTURE_URL" -o $J/capture.bin
stage frames
mkdir -p $J/raw
if unzip -tq $J/capture.bin >/dev/null 2>&1; then unzip -q $J/capture.bin -d $J/raw; IMG=$(dirname "$(find $J/raw -iname '*.jp*g' | head -1)")
else python3 $VW/frames.py $J/capture.bin $J/raw --fps 3 --max-side 1600; IMG=$J/raw/images; fi
mkdir -p $J/sfm_in && for f in "$IMG"/*; do ffmpeg -loglevel error -y -i "$f" -vf "scale='min(800,iw)':-2" "$J/sfm_in/$(basename "$f")"; done
stage sfm
/opt/sfm/bin/python $VW/sfm.py --sfm_images $J/sfm_in --train_images "$IMG" --out $J/scene
stage train
bash $VW/train_scene.sh $J/scene $J/out/run "$ITERS" "$CAP_MAX"; cp $J/out/run/timing.json $J/out/timing.json
stage pack
PLY=$(ls $J/out/run/ply/*.ply | tail -1)
bash $VW/pack.sh "$PLY" $J/out/room 15 > /dev/null; cp $J/out/room.pack.json $J/out/pack.json
stage qc
GSPLAT_EXAMPLES=/root/gsplat/examples python3 $VW/qc.py --scene $J/scene --run $J/out/run --sog $J/out/room.sog \
  --spz $J/out/room.spz --sfm-stats $J/scene/sfm_stats.json --out $J/out/qc.json
stage upload
put $J/out/room.sog room.sog; put $J/out/room.spz room.spz; put "$PLY" room.ply; put $J/out/qc.json qc.json
STATUS=ok
