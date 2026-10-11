"""Pilot runner: one Secure Cloud 4090 pod, scenes trained sequentially over SSH, outputs copied back, pod ALWAYS terminated.

Used for the 2026-10-11 public-scene pilot because the box is not reachable from the internet and we did not want to put
test data in production storage. Production uses launch_job.py (URL in, URLs out).

  bash -lc 'python3 launcher/pilot_ssh.py --scene-root /workspace/visit-pilot/work --out /workspace/visit-pilot/runs \
            --scenes room kitchen counter --iters 15000 --max-minutes 80'

Safety: (1) try/finally terminate, (2) atexit + SIGTERM/SIGINT handlers, (3) a detached watchdog process that terminates the
pod after --max-minutes no matter what happens to this process, (4) a hard spend check (aborts if projected cost > --max-usd).
"""
import argparse, atexit, json, os, signal, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import runpod_api as rp

HERE = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--scene-root", required=True)
ap.add_argument("--scenes", nargs="+", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--iters", type=int, default=15000)
ap.add_argument("--cap-max", type=int, default=1000000)
ap.add_argument("--max-minutes", type=float, default=80)
ap.add_argument("--max-usd", type=float, default=3.0)
ap.add_argument("--key", default=os.path.expanduser("/workspace/visit-pilot/keys/pod_ed25519"))
a = ap.parse_args()

out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
log = {"events": [], "scenes": {}}
def ev(name, **kw):
    e = {"t": time.time(), "name": name, **kw}; log["events"].append(e)
    print(time.strftime("%H:%M:%S"), name, json.dumps(kw) if kw else "", flush=True)
    json.dump(log, open(out / "pilot_log.json", "w"), indent=2)

pod_id = None
terminated = False
def cleanup(*_):
    global terminated
    if pod_id and not terminated:
        ok = rp.terminate_pod(pod_id)
        terminated = ok
        ev("terminate", pod=pod_id, ok=ok)
        json.dump(log, open(out / "pilot_log.json", "w"), indent=2)
    if _ and _[0] in (signal.SIGTERM, signal.SIGINT):
        sys.exit(1)
atexit.register(cleanup)
signal.signal(signal.SIGTERM, cleanup); signal.signal(signal.SIGINT, cleanup)

def ssh_base(ip, port):
    return ["ssh", "-i", a.key, "-p", str(port), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR", f"root@{ip}"]

def push(ip, port, src_dir, names, remote_dir, excludes=()):
    """tar-over-ssh (the stock image may not have rsync)."""
    ex = " ".join(f"--exclude={e}" for e in excludes)
    cmd = f"tar -C {src_dir} {ex} -cf - {' '.join(names)} | " + " ".join(ssh_base(ip, port)) + f" 'mkdir -p {remote_dir} && tar -C {remote_dir} -xf -'"
    subprocess.run(["bash", "-o", "pipefail", "-c", cmd], check=True)

def pull(ip, port, remote_dir, local_dir, excludes=()):
    ex = " ".join(f"--exclude={e}" for e in excludes)
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    cmd = " ".join(ssh_base(ip, port)) + f" 'tar -C {remote_dir} {ex} -cf - .' | tar -C {local_dir} -xf -"
    return subprocess.run(["bash", "-o", "pipefail", "-c", cmd]).returncode

def sh(ip, port, cmd, check=True, **kw):
    return subprocess.run(ssh_base(ip, port) + [cmd], check=check, **kw)

bal0 = rp.balance(); ev("balance_before", **bal0)
pub = Path(a.key + ".pub").read_text().strip()
name = f"bricks-visit-pilot-{int(time.time())}"
try:
    pod = rp.create_pod(name, env={"PUBLIC_KEY": pub})
    pod_id = pod["id"]
    (out / "pod_id.txt").write_text(pod_id)
    cost_hr = float(pod.get("costPerHr") or 0.89)
    ev("pod_created", pod=pod_id, costPerHr=cost_hr, dc=(pod.get("machine") or {}).get("dataCenterId"))
    if cost_hr * a.max_minutes / 60 > a.max_usd:
        raise RuntimeError(f"projected cost {cost_hr * a.max_minutes / 60:.2f} > max-usd")
    # detached watchdog: survives this process
    wd = subprocess.Popen(["bash", "-c", f"sleep {int(a.max_minutes * 60)}; python3 {HERE}/launcher/runpod_api.py terminate {pod_id}"],
                          start_new_session=True, stdout=open(out / "watchdog.log", "w"), stderr=subprocess.STDOUT)
    ev("watchdog_started", pid=wd.pid, minutes=a.max_minutes)
    t_create = time.time()
    ip, port, p = rp.wait_ssh_endpoint(pod_id)
    ev("ssh_endpoint", ip=ip, port=port, dc=(p.get("machine") or {}).get("dataCenterId"))
    for i in range(60):
        r = subprocess.run(ssh_base(ip, port) + ["echo ok"], capture_output=True, text=True)
        if r.returncode == 0: break
        time.sleep(5)
    else:
        raise RuntimeError("ssh never came up")
    ev("ssh_ready", secs_from_create=round(time.time() - t_create, 1))
    sh(ip, port, "mkdir -p /root/vw /root/scenes /root/out && nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader")
    push(ip, port, str(HERE), ["requirements-train.txt"], "/root/vw")
    push(ip, port, str(HERE / "worker"), ["."], "/root/vw")
    boot = subprocess.Popen(ssh_base(ip, port) + ["bash /root/vw/bootstrap.sh"], stdout=open(out / "bootstrap.log", "w"),
                            stderr=subprocess.STDOUT)
    t_up = time.time()
    for s in a.scenes:  # upload while bootstrap installs
        push(ip, port, a.scene_root, [s], "/root/scenes", excludes=("sfm_work",))
    ev("upload_done", secs=round(time.time() - t_up, 1))
    if boot.wait() != 0:
        raise RuntimeError("bootstrap failed, see bootstrap.log")
    ev("bootstrap_done", secs_from_create=round(time.time() - t_create, 1))
    for s in a.scenes:
        spent_min = (time.time() - t_create) / 60
        if cost_hr * spent_min / 60 > a.max_usd:
            raise RuntimeError("spend cap reached")
        ev("train_start", scene=s)
        t0 = time.time()
        r = sh(ip, port, f"bash /root/vw/train_scene.sh /root/scenes/{s} /root/out/{s} {a.iters} {a.cap_max}", check=False,
               capture_output=True, text=True)
        dt = time.time() - t0
        (out / s).mkdir(exist_ok=True)
        pull(ip, port, f"/root/out/{s}", str(out / s), excludes=("./ckpts", "./tb"))
        log["scenes"][s] = {"train_ssh_wall_s": round(dt, 1), "rc": r.returncode, "stdout_tail": r.stdout[-1500:],
                            "stderr_tail": r.stderr[-1500:]}
        ev("train_done", scene=s, secs=round(dt, 1), rc=r.returncode)
    ev("all_done", secs_from_create=round(time.time() - t_create, 1))
except Exception as e:
    ev("error", msg=repr(e)[:800])
    raise
finally:
    cleanup()
    try:
        os.killpg(wd.pid, signal.SIGTERM)
    except Exception:
        pass
    left = [p for p in rp.list_pods() if p.get("id") == pod_id]
    ev("post_check", pod_still_listed=bool(left))
    try:
        ev("balance_after_immediate", **rp.balance())
    except Exception as e:
        ev("balance_err", msg=repr(e))
