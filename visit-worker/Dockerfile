# Bricks Virtual Visit worker: GLOMAP/COLMAP (pycolmap) + gsplat 1.5.3 + splat-transform (SOG/SPZ) + QC.
# Base image is the Runpod PyTorch 2.1 image (usually cached on Runpod hosts, so pulls are fast).
FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive VW=/opt/vw TORCH_CUDA_ARCH_LIST="8.6;8.9"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg unzip curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/* && \
    npm i -g @playcanvas/splat-transform@3.10.1
# SfM lives in its own venv: official pycolmap (GLOMAP global mapper) conflicts by name with the pycolmap fork gsplat parses with.
RUN python -m venv /opt/sfm && /opt/sfm/bin/pip install --no-cache-dir "pycolmap==4.2.1" numpy pillow
COPY requirements-train.txt /opt/vw/requirements-train.txt
RUN pip install --no-cache-dir --no-deps -r /opt/vw/requirements-train.txt && \
    pip install --no-cache-dir --no-build-isolation --no-deps "git+https://github.com/rahul-goel/fused-ssim@328dc9836f513d00c4b5bc38fe30478b4435cbb5" && \
    git clone -q --depth 1 --branch v1.5.3 https://github.com/nerfstudio-project/gsplat.git /root/gsplat
COPY worker/ /opt/vw/
RUN chmod +x /opt/vw/*.sh
CMD ["/opt/vw/entry_job.sh"]
