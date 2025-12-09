# Distributed Video Analytics Pipeline

This repository implements a **scalable video analytics pipeline** built on NVIDIA **Cosmos‑Reason1** vision‑language model. The system captures RTSP streams, buffers 5‑second video chunks, pushes them to Redis, and runs inference on a DGX node.

## Architecture
- **Redis**: Message queue for video chunks.
- **RTSP Simulator**: `MediaMTX` + FFmpeg to generate sample streams.
- **Streamer**: Python service (`src/streamer/main.py`) that captures, buffers (5 s), serializes, and pushes video data to Redis.
- **Inference**: Python service (`src/inference/main.py`) that pulls video chunks, runs Cosmos‑Reason1 inference (batch mode) and outputs reports.
- **Kubernetes**: Manifests in `k8s/` deploy Redis, RTSP simulator, streamer, and inference pods with GPU affinity.
- **Docker**: Custom image patches `vllm` for Cosmos‑Reason1 and includes all dependencies.

## Quick Start
```bash
# Build Docker image
docker build -t cosmos-reason1-server .

# Deploy to Kubernetes
kubectl apply -k k8s/
```

The pipeline will automatically process the videos placed in `videos/` and generate JSON reports in `reports/`.

## Development
- **Python**: 3.10+, see `requirements.txt`.
- **Dependencies**: `opencv-python`, `redis`, `vllm`, `qwen-vl-utils`, `ultralytics` (YOLO placeholder).
- **Run locally**:
  ```bash
  python3 src/streamer/main.py
  python3 src/inference/main.py
  ```

## License
Apache‑2.0. See `LICENSE` for details.
