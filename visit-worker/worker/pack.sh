#!/usr/bin/env bash
# Compress a trained .ply to .sog (PlayCanvas SOG, CPU k-means) and .spz (v3, for GO deliver / Spark).
# If the .sog exceeds the streaming budget, decimate and re-pack once.  usage: pack.sh in.ply out_prefix [budget_mb]
set -euo pipefail
IN=$1; OUT=$2; BUDGET=${3:-15}
ST=${SPLAT_TRANSFORM:-splat-transform}
t0=$(date +%s.%N)
$ST -q -w -g cpu "$IN" -N "$OUT.sog"
t1=$(date +%s.%N)
$ST -q -w "$IN" -N --spz-version 3 "$OUT.spz"
t2=$(date +%s.%N)
SZ=$(stat -c %s "$OUT.sog"); DEC=""
if [ "$SZ" -gt $(( BUDGET * 1000000 )) ]; then
  PCT=$(python3 -c "print(max(20, int(100 * $BUDGET * 1e6 / $SZ * 0.93)))")
  # decimate needs a .ply output (CPU path works without a GPU), then re-pack
  $ST -q -w -g cpu "$IN" -N -d "${PCT}%" "$OUT.dec.ply"
  $ST -q -w -g cpu "$OUT.dec.ply" "$OUT.sog"; DEC="$PCT"; rm -f "$OUT.dec.ply"
fi
t3=$(date +%s.%N)
python3 -c "import json;print(json.dumps({'sog_s':round($t1-$t0,1),'spz_s':round($t2-$t1,1),'redecimate_s':round($t3-$t2,1),'decimated_pct':'$DEC' or None,'sog_bytes':$(stat -c %s "$OUT.sog"),'spz_bytes':$(stat -c %s "$OUT.spz")}))" | tee "$OUT.pack.json"
