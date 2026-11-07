"""Measured cost per scene/home from pilot_log.json. usage: cost.py runs_dir rooms_per_home"""
import json, sys
d = json.load(open(sys.argv[1] + "/pilot_log.json")); rooms = int(sys.argv[2]) if len(sys.argv) > 2 else 8
ev = {e["name"] + (":" + e.get("scene", "")): e for e in d["events"]}
rate = ev["pod_created:"]["costPerHr"]
t_create = ev["pod_created:"]["t"]; t_end = ev["terminate:"]["t"]
pod_min = (t_end - t_create) / 60
overhead_min = (ev["bootstrap_done:"]["t"] - t_create) / 60
scenes = {k.split(":")[1]: v["secs"] / 60 for k, v in ev.items() if k.startswith("train_done:")}
avg_train = sum(scenes.values()) / len(scenes)
per_scene_marginal = avg_train / 60 * rate
per_scene_alloc = pod_min / 60 * rate / len(scenes)
home_min = overhead_min + rooms * avg_train + 5  # + base splat ~5 min
r = {"rate_usd_hr": rate, "pod_minutes": round(pod_min, 1), "pod_cost_usd": round(pod_min / 60 * rate, 3),
     "startup_bootstrap_min": round(overhead_min, 1), "train_min_per_scene": {k: round(v, 1) for k, v in scenes.items()},
     "cost_per_scene_marginal_usd": round(per_scene_marginal, 3), "cost_per_scene_allocated_usd": round(per_scene_alloc, 3),
     "rooms_per_home": rooms, "gpu_min_per_home": round(home_min, 1), "cost_per_home_usd": round(home_min / 60 * rate, 2)}
print(json.dumps(r, indent=2))
