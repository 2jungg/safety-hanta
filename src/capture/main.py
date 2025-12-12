import os
import time
import cv2
import redis
import threading
import uuid
import json
import logging

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
RTSP_URLS = os.getenv("RTSP_URLS", "").split(",")
QUEUE_NAME = "video_stream_queue"
BUFFER_DURATION = 15.0  # seconds
TEMP_VIDEO_DIR = "/videos/temp_video"

# Ensure temp dir exists
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("capture")

def get_redis_client():
    try:
        return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return None

def process_stream(stream_url, redis_client):
    """
    Captures video from stream_url, buffers for BUFFER_DURATION, 
    encodes to mp4, and pushes to Redis.
    """
    stream_id = stream_url.split("/")[-1] # Simple ID derivation
    logger.info(f"Starting capture for {stream_id} ({stream_url})")
    
    cap = None
    while True:
        cap = cv2.VideoCapture(stream_url)
        if cap.isOpened():
            break
        logger.warning(f"Could not open stream {stream_id}. Retrying in 5 seconds...")
        cap.release()
        time.sleep(5)

    # Get stream properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # Fallback if FPS is invalid
    if fps <= 0:
        fps = 30.0
    
    # Initialize streaming state
    start_time = time.time()
    frame_count = 0
    
    # Setup initial VideoWriter
    filename = f"{stream_id}_{uuid.uuid4()}.mp4"
    file_path = os.path.join(TEMP_VIDEO_DIR, filename)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
    out = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
    
    while True:
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"Failed to read frame from {stream_id}. Reconnecting...")
            
            # Close partial file and delete to avoid corruption
            if out:
                out.release()
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            
            cap.release()
            time.sleep(2)
            
            # Attempt to reconnect
            cap = cv2.VideoCapture(stream_url)
            # If cap isn't opened immediately, the next read() will fail and we loop again. This is fine.
            
            # Reset chunk state
            start_time = time.time()
            frame_count = 0
            filename = f"{stream_id}_{uuid.uuid4()}.mp4"
            file_path = os.path.join(TEMP_VIDEO_DIR, filename)
            out = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
            continue
        
        # Write frame immediately
        out.write(frame)
        frame_count += 1
        
        current_time = time.time()
        elapsed = current_time - start_time
        
        if elapsed >= BUFFER_DURATION:
            # Finalize current chunk
            out.release()
            
            if frame_count > 0:
                try:
                    # Send payload
                    payload = {
                        "stream_id": stream_id,
                        "timestamp": start_time,
                        "duration": elapsed,
                        "video_path": file_path
                    }
                    if redis_client:
                        redis_client.rpush(QUEUE_NAME, json.dumps(payload))
                        logger.info(f"Pushed {elapsed:.2f}s ({frame_count} frames) from {stream_id} to Redis.")
                except Exception as e:
                    logger.error(f"Error processing chunk: {e}")
            else:
                 # Empty chunk?
                 if os.path.exists(file_path):
                    os.remove(file_path)
            
            # Start next chunk
            start_time = time.time()
            frame_count = 0
            filename = f"{stream_id}_{uuid.uuid4()}.mp4"
            file_path = os.path.join(TEMP_VIDEO_DIR, filename)
            out = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
            
        # Optional: Sleep to prevent tight loop if FPS is high? 
        # Actually cv2.read() is blocking usually based on stream FPS.
        
def main():
    logger.info("Streamer service starting...")
    redis_client = get_redis_client()
    
    # Wait for Redis
    while True:
        try:
            if redis_client.ping():
                logger.info("Connected to Redis.")
                break
        except Exception:
            logger.warning("Waiting for Redis...")
            time.sleep(2)
            redis_client = get_redis_client()
            
    # Sharding Logic: Process ONLY the stream corresponding to this worker's index
    hostname = os.getenv("HOSTNAME", "capture-worker-1")
    try:
        # Expected hostname format: capture-worker-{index}
        # We expect index to start from 1 (capture-worker-1 -> cam1)
        worker_id = int(hostname.split("-")[-1])
        
        # Calculate array index (0-based)
        # capture-worker-1 -> id 1 -> index 0
        url_index = worker_id - 1
        
        if 0 <= url_index < len(RTSP_URLS):
            target_url = RTSP_URLS[url_index]
            logger.info(f"Worker {hostname} (ID: {worker_id}) assigned to {target_url} (Index: {url_index})")
            
            # Run processing in main thread since we only have one stream
            if target_url:
                process_stream(target_url.strip(), redis_client)
        else:
            logger.error(f"Worker ID {worker_id} is out of range for {len(RTSP_URLS)} RTSP URLs.")
            
    except ValueError:
        logger.error(f"Could not parse worker ID from hostname: {hostname}. Fallback to processing all streams?")
        # Fallback or exit? For now, exit to avoid duplication
        return
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping streamer service.")

if __name__ == "__main__":
    main()
