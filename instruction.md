[System Role] You are a Senior MLOps Engineer and Kubernetes Specialist. You are architecting a distributed video analytics pipeline. The system must be scalable, robust, and designed to run on a cluster containing High-Performance Computing nodes (NVIDIA DGX).

[Project Architecture Overview] The pipeline consists of three stages:

Source (Simulation): 10 Simulated RTSP streams (from local video filea).

Ingestion (K8s Streamer Pods): A Kubernetes Deployment that captures RTSP streams, aggregates frames into 5-second time windows, and pushes the data to a Message Queue.

Message Broker: Redis (used as a buffer/queue).

Inference (DGX Worker): A Python consumer service running on a DGX node that pulls the 5-second chunks from Redis and performs AI inference.

[Detailed Requirements]

1. Infrastructure & RTSP Simulation (Kubernetes)

Create a K8s manifest for MediaMTX to act as the RTSP server.

Create a "Simulator Pod" that uses FFmpeg to loop a local video file (sample.mp4) and publish it to 10 distinct RTSP paths (rtsp://mediamtx-service:8554/cam0 to cam9).

2. Streamer Pods (The Producer)

Deployment Type: Kubernetes Deployment (scalable).

Logic:

Each pod (or a simplified manager) connects to assigned RTSP streams.

5-Second Buffering Strategy: DO NOT stream frame-by-frame. Instead, accumulate frames for exactly 5 seconds.

Serialization: Serialize this 5-second batch (e.g., using pickle or converting to a byte stream of a video container like .mp4 or .ts).

Push to Redis: Push the serialized binary data to a Redis List (Key: video_stream_queue).

Environment Variables: Use ConfigMaps to pass RTSP URLs and Redis endpoints.

3. Message Queue (Redis)

Provide a standard K8s deployment YAML for Redis.

Ensure the service is accessible by DNS (redis-service).

4. Inference Engine (The Consumer - DGX Optimized)

Node Affinity: This Pod must be scheduled on the DGX node (Use nodeSelector or tolerations assuming a label like accelerator: nvidia-gpu).

Logic:

Connect to Redis and block-pop (BLPOP) items from video_stream_queue.

Deserialize the 5-second video chunk.

Perform Object Detection (Use a placeholder YOLOv8 model).

Since the input is a 5-second batch, utilize the GPU efficiently (batch inference).

[Deliverables] Please provide the complete code and configuration files:

k8s/01-redis.yaml: Redis Deployment & Service.

k8s/02-rtsp-sim.yaml: MediaMTX & FFmpeg Simulator Deployment.

k8s/03-streamer.yaml: The Python Streamer Deployment (Ingestion).

k8s/04-inference.yaml: The Inference Deployment (with GPU limits/requests).

src/streamer/main.py: Python script for 5s buffering & Redis push.

src/inference/main.py: Python script for Redis pull & GPU inference.

Dockerfile: A unified or separate Dockerfile for the Python services.

[Technical Constraints]

Use Python 3.10+.

Handle network instability (try-catch-reconnect logic in Streamer).

Ensure the 5-second window is strictly time-based (use time.time() logic), not just frame-count based, to account for FPS fluctuations.