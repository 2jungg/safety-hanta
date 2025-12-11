import os
import time
import cv2
import redis
import threading
import tempfile
import base64
import json
import logging

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
RTSP_URLS = os.getenv("RTSP_URLS", "").split(",")
QUEUE_NAME = "video_stream_queue"
BUFFER_DURATION = 5.0  # seconds

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
    
    frame_buffer = []
    start_time = time.time()
    
    while True:
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"Failed to read frame from {stream_id}. Reconnecting...")
            cap.release()
            time.sleep(2)
            cap = cv2.VideoCapture(stream_url)
            continue
        
        frame_buffer.append(frame)
        current_time = time.time()
        elapsed = current_time - start_time
        
        if elapsed >= BUFFER_DURATION:
            # strictly time-based flush
            if frame_buffer:
                # Write frames to a temporary mp4 file
                try:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                        tmp_filename = tmp_file.name
                    
                    # Define codec
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v') # or 'avc1' or 'h264' if available
                    out = cv2.VideoWriter(tmp_filename, fourcc, fps, (width, height))
                    
                    for f in frame_buffer:
                        out.write(f)
                    out.release()
                    
                    # Read binary data
                    with open(tmp_filename, "rb") as f:
                        video_bytes = f.read()
                    
                    # Cleanup temp file
                    os.remove(tmp_filename)
                    
                    # Prepare payload
                    # We encode bytes to base64 to store in JSON
                    payload = {
                        "stream_id": stream_id,
                        "timestamp": start_time,
                        "duration": elapsed,
                        "video_data_base64": base64.b64encode(video_bytes).decode('utf-8')
                    }
                    
                    # Push to Redis
                    if redis_client:
                        redis_client.rpush(QUEUE_NAME, json.dumps(payload))
                        logger.info(f"Pushed {elapsed:.2f}s chunk from {stream_id} to Redis.")
                    
                except Exception as e:
                    logger.error(f"Error processing batch for {stream_id}: {e}")
            
            # Reset buffer
            frame_buffer = []
            start_time = time.time() # Reset strictly to now
            
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
            
    threads = []
    for url in RTSP_URLS:
        if url:
            t = threading.Thread(target=process_stream, args=(url.strip(), redis_client))
            t.daemon = True
            t.start()
            threads.append(t)
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping streamer service.")

if __name__ == "__main__":
    main()
