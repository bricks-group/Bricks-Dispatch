"""Box-CPU SfM for the visit pilot: SIFT + exhaustive/sequential matching + GLOMAP (pycolmap.global_mapping),
fallback to incremental COLMAP. Writes a gsplat-ready scene dir: <out>/images (train-res) + <out>/sparse/0."""
import sys, os, time, json, shutil, argparse
from pathlib import Path
import pycolmap

ap = argparse.ArgumentParser()
ap.add_argument("--sfm_images", required=True)      # low-res images used for SfM
ap.add_argument("--train_images", required=True)    # higher-res images used for training (same names)
ap.add_argument("--out", required=True)
ap.add_argument("--matcher", default="exhaustive", choices=["exhaustive", "sequential"])
ap.add_argument("--mapper", default="global", choices=["global", "incremental"])
a = ap.parse_args()

out = Path(a.out); work = out / "sfm_work"; work.mkdir(parents=True, exist_ok=True)
db = work / "database.db"
if db.exists(): db.unlink()
t = {}; t0 = time.time()
reader = pycolmap.ImageReaderOptions(); reader.camera_model = "SIMPLE_RADIAL"
ext = pycolmap.FeatureExtractionOptions(); ext.num_threads = os.cpu_count()
ext.sift.max_num_features = 8192
pycolmap.extract_features(db, a.sfm_images, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader,
                          extraction_options=ext, device=pycolmap.Device.cpu)
t["extract_s"] = time.time() - t0; t1 = time.time()
mo = pycolmap.FeatureMatchingOptions(); mo.num_threads = os.cpu_count()
if a.matcher == "exhaustive":
    pycolmap.match_exhaustive(db, matching_options=mo, device=pycolmap.Device.cpu)
else:
    po = pycolmap.SequentialPairingOptions(); po.overlap = 20; po.loop_detection = False
    pycolmap.match_sequential(db, matching_options=mo, pairing_options=po, device=pycolmap.Device.cpu)
t["match_s"] = time.time() - t1; t2 = time.time()
sp = work / "sparse"; sp.mkdir(exist_ok=True)
recs = None; mapper_used = a.mapper
try:
    if a.mapper == "global":
        recs = pycolmap.global_mapping(db, a.sfm_images, sp)
except Exception as e:
    print("global mapping failed:", e)
if not recs:
    mapper_used = "incremental"
    recs = pycolmap.incremental_mapping(db, a.sfm_images, sp)
t["map_s"] = time.time() - t2
best = max(recs.values(), key=lambda r: r.num_reg_images())
n_imgs = len([f for f in os.listdir(a.sfm_images) if f.lower().endswith((".jpg", ".jpeg", ".png"))])
stats = dict(mapper=mapper_used, matcher=a.matcher, models=len(recs), registered=best.num_reg_images(), total=n_imgs,
             reg_ratio=best.num_reg_images() / n_imgs, points=best.num_points3D(),
             mean_reproj_px=best.compute_mean_reprojection_error(), timings=t, total_s=time.time() - t0)
# rescale cameras to training resolution
from PIL import Image
first = next(iter(best.images.values())).name
W, H = Image.open(os.path.join(a.train_images, first)).size
for cid, cam in best.cameras.items():
    s = W / cam.width
    stats["scale"] = s
    cam.rescale(W, H)
# reprojection error in train px is scaled; report SfM px (above)
s0 = out / "sparse" / "0"; s0.mkdir(parents=True, exist_ok=True)
best.write(s0)
imgdir = out / "images"
if imgdir.exists(): shutil.rmtree(imgdir)
imgdir.mkdir()
for img in best.images.values():  # only registered images go to training
    shutil.copy2(os.path.join(a.train_images, img.name), imgdir / img.name)
json.dump(stats, open(out / "sfm_stats.json", "w"), indent=2)
print(json.dumps(stats, indent=2))
