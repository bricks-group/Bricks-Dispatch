"""Minimal Runpod REST/GraphQL client (stdlib only). Reads RUNPOD_API_KEY from the environment; never logs it."""
import json, os, time, urllib.request, urllib.error

REST = "https://rest.runpod.io/v1"
GQL = "https://api.runpod.io/graphql"
GPU_4090 = "NVIDIA GeForce RTX 4090"
DEFAULT_IMAGE = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04"


def _key():
    k = os.environ.get("RUNPOD_API_KEY")
    if not k:
        raise RuntimeError("RUNPOD_API_KEY not set")
    return k


def _req(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
                                        "User-Agent": "bricks-visit-worker/0.1"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt.strip() else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"error": txt[:500]}


def balance():
    _, d = _req("POST", GQL, {"query": "{ myself { clientBalance currentSpendPerHr } }"})
    return d["data"]["myself"]


def create_pod(name, image=DEFAULT_IMAGE, env=None, gpu=GPU_4090, cloud="SECURE", container_gb=40,
               ports=("22/tcp",), docker_start_cmd=None):
    if cloud != "SECURE":
        raise ValueError("Bricks policy: client interiors only on Secure Cloud")
    body = {"name": name, "imageName": image, "cloudType": "SECURE", "computeType": "GPU", "gpuCount": 1,
            "gpuTypeIds": [gpu], "interruptible": False, "containerDiskInGb": container_gb, "volumeInGb": 0,
            "ports": list(ports), "env": env or {}}
    if docker_start_cmd:
        body["dockerStartCmd"] = docker_start_cmd
    code, d = _req("POST", f"{REST}/pods", body)
    if code not in (200, 201):
        raise RuntimeError(f"create_pod failed {code}: {d}")
    return d


def get_pod(pod_id):
    code, d = _req("GET", f"{REST}/pods/{pod_id}")
    return code, d


def list_pods():
    _, d = _req("GET", f"{REST}/pods")
    return d or []


def terminate_pod(pod_id, verify=True):
    """DELETE = terminate (not stop). Retries; returns True once the pod is gone."""
    for attempt in range(8):
        code, d = _req("DELETE", f"{REST}/pods/{pod_id}")
        if code in (200, 204, 404):
            if not verify:
                return True
            gcode, g = get_pod(pod_id)
            if gcode == 404 or (isinstance(g, dict) and g.get("desiredStatus") == "TERMINATED") or not g:
                return True
        time.sleep(3 * (attempt + 1))
    return False


def wait_ssh_endpoint(pod_id, timeout_s=900):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        code, p = get_pod(pod_id)
        if code == 200 and p and p.get("publicIp") and (p.get("portMappings") or {}).get("22"):
            return p["publicIp"], int(p["portMappings"]["22"]), p
        time.sleep(5)
    raise TimeoutError("pod never exposed ssh")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1]
    if cmd == "balance":
        print(json.dumps(balance()))
    elif cmd == "list":
        print(json.dumps([{k: p.get(k) for k in ("id", "name", "desiredStatus", "costPerHr")} for p in list_pods()]))
    elif cmd == "terminate":
        print("terminated" if terminate_pod(sys.argv[2]) else "TERMINATE_FAILED")
    elif cmd == "terminate-prefix":  # sweeper: terminate every pod whose name starts with prefix
        for p in list_pods():
            if (p.get("name") or "").startswith(sys.argv[2]):
                print(p["id"], "terminated" if terminate_pod(p["id"]) else "TERMINATE_FAILED")
