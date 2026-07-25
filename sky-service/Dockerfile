FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# model/skyseg_int8.onnx (44MB) is committed directly to this repo --
# no runtime download, no HF dependency at build or start time.
COPY . .

RUN useradd -m -u 1000 -s /bin/bash bricks && chown -R bricks:bricks /app
USER bricks

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
