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
QUEUE_NAME = "video_stream_queue"
BUFFER_DURATION = float(os.getenv("BUFFER_DURATION", "10"))  # seconds
TEMP_VIDEO_DIR = "/videos/temp_video"
RTSP_BASE_URL_DEFAULT = os.getenv("RTSP_BASE_URL_DEFAULT", "rtsp://localhost:8554/cam") # Default for local testing

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

def process_stream(stream_url, redis_client, display_stream_id):
    """
    Captures video from stream_url, buffers for BUFFER_DURATION, 
    encodes to mp4, and pushes to Redis.
    """
    stream_id = display_stream_id # Use the provided display_stream_id
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
    rtsp_base_url = os.getenv("RTSP_BASE_URL", RTSP_BASE_URL_DEFAULT) # e.g., "rtsp://service:8554/cam"
    
    try:
        # Expected hostname format: capture-worker-{index}
        # We expect index to start from 0 for StatefulSet ordinals usually, but let's check.
        # If StatefulSet name is "capture-worker", pods are "capture-worker-0", "capture-worker-1"...
        # The user's previous code assumed "capture-worker-1" -> ID 1.
        # Let's handle generic "capture-worker-{N}" format.
        
        parts = hostname.split("-")
        worker_id = int(parts[-1])
        
        # Dynamic Mode (now the only mode)
        cam_index = worker_id 
        target_url = f"{rtsp_base_url}{cam_index}" # Match simulator's 1-based indexing and direct path
        display_stream_id = f"cam{cam_index}" # Custom ID for display, consistent with simulator
        logger.info(f"Worker {hostname} (ID: {worker_id}) assigned to Dynamic URL: {target_url} with display ID: {display_stream_id}")
        
        process_stream(target_url.strip(), redis_client, display_stream_id)
            
    except ValueError:
        logger.error(f"Could not parse worker ID from hostname: {hostname}. Fallback to processing all streams?")
        return
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping streamer service.")

if __name__ == "__main__":
    main()

