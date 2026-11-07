"""Automatic QC for one trained room. Produces qc.json with metrics, per-gate results and verdict pass|hold.
A 'hold' maps to Dispatch pipeline_qc.passed=false -> 422 visit_pipeline_qc_held -> human review (never auto-delivered).

usage: python qc.py --scene <scene_dir with sparse/0> --run <gsplat result dir> --sog x.sog [--spz x.spz]
                    [--sfm-stats sfm_stats.json] [--shot viewer.png] --out qc.json
Needs: numpy, plyfile, pillow, and gsplat's examples/datasets on PYTHONPATH (for the normalized frame).
"""
import argparse, glob, json, os, sys
import numpy as np

GATES = {  # threshold, direction; rationale in README.md
    "reg_ratio":            (0.85, ">="),  # >=85% of frames registered
    "mean_reproj_px":       (1.0,  "<="),  # SfM mean reprojection error (SfM-resolution px)
    "sfm_models":           (1,    "=="),  # one connected model per floor
    "holdout_psnr":         (25.0, ">="),  # every 8th frame never trained on
    "holdout_ssim":         (0.85, ">="),
    "outside_bbox_ratio":   (0.03, "<="),  # visible splats outside padded room bbox
    "big_faint_ratio":      (0.05, "<="),  # large, nearly transparent splats (fog / floaters)
    "sog_mb":               (15.0, "<="),  # streaming budget per room
    "render_luma":          ((0.06, 0.94), "in"),
    "render_coverage":      (0.60, ">="),
}

def check(name, v):
    thr, op = GATES[name]
    if v is None: return None
    if op == ">=": return v >= thr
    if op == "<=": return v <= thr
    if op == "==": return v == thr
    if op == "in": return thr[0] <= v <= thr[1]

def load_ply(path):
    from plyfile import PlyData
    v = PlyData.read(path)["vertex"].data
    xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32)
    op = 1 / (1 + np.exp(-v["opacity"].astype(np.float32)))
    sc = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1).astype(np.float32))
    return xyz, op, sc

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--sog"); ap.add_argument("--spz"); ap.add_argument("--sfm-stats"); ap.add_argument("--shot")
    ap.add_argument("--gsplat-examples", default=os.environ.get("GSPLAT_EXAMPLES", "/root/gsplat/examples"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    m = {}
    if a.sfm_stats and os.path.exists(a.sfm_stats):
        s = json.load(open(a.sfm_stats))
        m.update(reg_ratio=s["reg_ratio"], mean_reproj_px=s["mean_reproj_px"], sfm_models=s["models"])
    val = sorted(glob.glob(os.path.join(a.run, "stats", "val_step*.json")))
    if val:
        v = json.load(open(val[-1]))
        m.update(holdout_psnr=v.get("psnr"), holdout_ssim=v.get("ssim"), holdout_lpips=v.get("lpips"), num_gs=v.get("num_GS"))
    ply = sorted(glob.glob(os.path.join(a.run, "ply", "*.ply")))
    if ply:
        xyz, op, sc = load_ply(ply[-1])
        # room bbox in gsplat's normalized frame = 2-98 pct of SfM points + camera centres, padded 10% per side
        sys.path.insert(0, a.gsplat_examples)
        from datasets.colmap import Parser
        p = Parser(a.scene, factor=1, normalize=True, test_every=8)
        ref = np.concatenate([p.points, p.camtoworlds[:, :3, 3]], 0)
        lo, hi = np.percentile(ref, 2, 0), np.percentile(ref, 98, 0)
        pad = 0.10 * (hi - lo); lo, hi = lo - pad, hi + pad
        vis = op > 0.1
        outside = np.any((xyz < lo) | (xyz > hi), 1)
        diag = float(np.linalg.norm(hi - lo))
        big = sc.max(1) > 0.02 * diag
        m.update(ply_mb=round(os.path.getsize(ply[-1]) / 1e6, 2), splats=int(len(xyz)),
                 outside_bbox_ratio=round(float((outside & vis).sum() / max(vis.sum(), 1)), 4),
                 big_faint_ratio=round(float((big & (op < 0.1)).sum() / len(xyz)), 4),
                 low_opacity_share=round(float((op < 0.05).mean()), 4), bbox_diag=round(diag, 3))
    if a.sog and os.path.exists(a.sog): m["sog_mb"] = round(os.path.getsize(a.sog) / 1e6, 2)
    if a.spz and os.path.exists(a.spz): m["spz_mb"] = round(os.path.getsize(a.spz) / 1e6, 2)
    if a.shot and os.path.exists(a.shot):
        from PIL import Image
        im = np.asarray(Image.open(a.shot).convert("RGB")).astype(np.float32) / 255
        # crop away viewer chrome margins (top/bottom 12%)
        h = im.shape[0]; im = im[int(.12 * h):int(.88 * h)]
        luma = im @ np.array([.2126, .7152, .0722])
        bg = np.median(np.concatenate([luma[:4].ravel(), luma[-4:].ravel()]))
        m["render_luma"] = round(float(luma.mean()), 3)
        m["render_coverage"] = round(float((np.abs(luma - bg) > 0.03).mean() + (luma.std() > 0.05) * 0), 3)
        # coverage = share of pixels that differ from the flat background colour; a blank canvas scores ~0
    gates = {k: check(k, m.get(k)) for k in GATES}
    hard_fail = [k for k, ok in gates.items() if ok is False]
    missing = [k for k, ok in gates.items() if ok is None]
    verdict = "pass" if not hard_fail and not missing else "hold"
    res = {"verdict": verdict, "hold_reasons": hard_fail + [f"missing:{k}" for k in missing], "metrics": m,
           "gates": {k: {"value": m.get(k), "threshold": GATES[k][0], "op": GATES[k][1], "ok": gates[k]} for k in GATES}}
    json.dump(res, open(a.out, "w"), indent=2)
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
