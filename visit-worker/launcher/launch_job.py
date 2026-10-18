"""Job launcher: capture URL in -> Runpod Secure 4090 pod -> output URLs out. ALWAYS terminates the pod.

  python3 launch_job.py --job-id J123 --capture-url "<signed GET>" --spec spec.json [--image ghcr.io/bricks/visit-worker:0.1]

spec.json (all URLs pre-signed by Dispatch, e.g. via /api/capture/visit/sign; PUT for the worker, GET for us):
  {"outputs": {"room.sog": {"put": "...", "get": "..."}, "room.spz": {...}, "room.ply": {...}, "qc.json": {...}},
   "result":  {"put": "...", "get": "..."}}
Prints a JSON result: status, qc verdict, output GET URLs, pod runtime and cost. Not wired to any production trigger yet.
Termination layers: finally-block DELETE, detached watchdog process, in-pod MAX_RUNTIME_S self-terminate, entry_job EXIT trap.
Spend guards: refuses before creating a pod when max-rate x max-minutes > --max-usd or the balance can't cover it plus
--reserve-usd; refuses a pod billing over --max-rate; stops polling (and terminates) once spend reaches --max-usd.
"""
import argparse, json, os, subprocess, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import runpod_api as rp

def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True); ap.add_argument("--capture-url", required=True)
    ap.add_argument("--spec", required=True)
    # No default: ghcr.io/bricks-group/visit-worker isn't pushed yet, and a pod stuck pulling a missing image bills
    # until the watchdog fires. Pass a pushed image, or use pilot_ssh.py (stock image + bootstrap.sh).
    ap.add_argument("--image", default=os.environ.get("VISIT_WORKER_IMAGE"))
    ap.add_argument("--iters", type=int, default=15000); ap.add_argument("--cap-max", type=int, default=1000000)
    ap.add_argument("--max-minutes", type=float, default=90); ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--max-rate", type=float, default=float(os.environ.get("VISIT_MAX_USD_HR", "0.99")),
                    help="refuse a pod that bills more than this per hour (Secure 4090 is ~0.59-0.69)")
    ap.add_argument("--reserve-usd", type=float, default=1.0, help="leave at least this much balance untouched")
    a = ap.parse_args()
    if not a.image:
        sys.exit("refused: no --image / VISIT_WORKER_IMAGE (the visit-worker image isn't in a registry yet)")
    # Pre-flight, before any pod exists (nothing bills yet): worst case = max rate x max minutes.
    worst = a.max_rate * a.max_minutes / 60
    if worst > a.max_usd:
        sys.exit(f"refused: worst case ${worst:.2f} ({a.max_minutes:.0f} min at ${a.max_rate}/hr) > --max-usd {a.max_usd}")
    bal = float((rp.balance() or {}).get("clientBalance") or 0)
    if bal < worst + a.reserve_usd:
        sys.exit(f"refused: balance ${bal:.2f} < worst case ${worst:.2f} + reserve ${a.reserve_usd:.2f}")
    spec = json.load(open(a.spec))
    worker_spec = {"outputs": {k: {"put": v["put"]} for k, v in spec["outputs"].items()}, "result": {"put": spec["result"]["put"]}}
    env = {"JOB_ID": a.job_id, "CAPTURE_URL": a.capture_url, "JOB_SPEC": json.dumps(worker_spec), "ITERS": str(a.iters),
           "CAP_MAX": str(a.cap_max), "MAX_RUNTIME_S": str(int(a.max_minutes * 60) - 120)}
    pod_id, t0, wd, res = None, time.time(), None, None
    try:
        pod = rp.create_pod(f"bricks-visit-{a.job_id}", image=a.image, env=env, ports=())
        pod_id = pod["id"]; cost_hr = float(pod.get("costPerHr") or a.max_rate)
        if cost_hr > a.max_rate or cost_hr * a.max_minutes / 60 > a.max_usd:
            raise RuntimeError(f"pod bills ${cost_hr}/hr: over --max-rate/--max-usd")  # finally terminates it
        wd = subprocess.Popen(["bash", "-c", f"sleep {int(a.max_minutes * 60)}; python3 {Path(__file__).parent}/runpod_api.py terminate {pod_id}"],
                              start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while time.time() - t0 < a.max_minutes * 60:
            res = fetch_json(spec["result"]["get"])
            if res: break
            if cost_hr * (time.time() - t0) / 3600 > a.max_usd:  # hard spend cap, independent of the timer
                break
            code, p = rp.get_pod(pod_id)
            if code == 404 or (p and p.get("desiredStatus") in ("EXITED", "TERMINATED")):
                time.sleep(10); res = fetch_json(spec["result"]["get"]); break
            time.sleep(20)
    finally:
        ok = rp.terminate_pod(pod_id) if pod_id else True
        if wd:
            try: os.killpg(wd.pid, 15)
            except Exception: pass
    mins = (time.time() - t0) / 60
    out = {"job_id": a.job_id, "pod_id": pod_id, "terminated": ok, "minutes": round(mins, 1),
           "est_cost_usd": round(mins / 60 * (cost_hr if pod_id else 0), 3), "status": (res or {}).get("status", "no_result"),
           "qc_verdict": ((res or {}).get("qc") or {}).get("verdict"),
           "outputs": {k: v["get"] for k, v in spec["outputs"].items()} if (res or {}).get("status") == "ok" else {}, "result": res}
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "ok" else 1

if __name__ == "__main__":
    sys.exit(main())
