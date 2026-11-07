"""Phone video -> training frames (box/pod CPU). ffmpeg at N fps, drop blurry (Laplacian variance) and near-duplicate
frames, optional room split from GO 'Next room' tap timestamps.

usage: python frames.py video.mp4 out_dir [--fps 3] [--max-side 1600] [--rooms rooms.json]
rooms.json (from GO capture): [{"name":"Kitchen","t0":0.0,"t1":61.5}, ...]  -> out_dir/<room>/images/*.jpg
"""
import argparse, json, os, shutil, subprocess, tempfile
import cv2, numpy as np

def extract(video, out, fps, max_side, t0=None, t1=None):
    os.makedirs(out, exist_ok=True)
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    if t0 is not None: cmd += ["-ss", str(t0)]
    if t1 is not None: cmd += ["-to", str(t1)]
    cmd += ["-i", video, "-vf", f"fps={fps},scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'",
            "-q:v", "2", os.path.join(out, "f_%05d.jpg")]
    subprocess.run(cmd, check=True)

def filter_frames(d, blur_pct=15, dup_thresh=0.985):
    files = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
    sharp, thumbs = [], []
    for f in files:
        g = cv2.imread(os.path.join(d, f), cv2.IMREAD_GRAYSCALE)
        sharp.append(cv2.Laplacian(g, cv2.CV_64F).var())
        thumbs.append(cv2.resize(g, (64, 36)).astype(np.float32).ravel())
    cut = np.percentile(sharp, blur_pct)  # drop the blurriest N% (motion blur from walking)
    keep, last, dropped = [], None, {"blur": 0, "dup": 0}
    for f, s, t in zip(files, sharp, thumbs):
        if s < cut: dropped["blur"] += 1; os.remove(os.path.join(d, f)); continue
        if last is not None:
            c = np.corrcoef(t, last)[0, 1]
            if c > dup_thresh: dropped["dup"] += 1; os.remove(os.path.join(d, f)); continue
        keep.append(f); last = t
    return {"kept": len(keep), "total": len(files), "dropped": dropped}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--fps", type=float, default=3); ap.add_argument("--max-side", type=int, default=1600)
    ap.add_argument("--rooms")
    a = ap.parse_args()
    report = {}
    if a.rooms:
        for r in json.load(open(a.rooms)):
            d = os.path.join(a.out, r["name"], "images")
            extract(a.video, d, a.fps, a.max_side, r.get("t0"), r.get("t1"))
            report[r["name"]] = filter_frames(d)
    else:
        d = os.path.join(a.out, "images"); extract(a.video, d, a.fps, a.max_side); report["all"] = filter_frames(d)
    json.dump(report, open(os.path.join(a.out, "frames_report.json"), "w"), indent=2)
    print(json.dumps(report, indent=2))
