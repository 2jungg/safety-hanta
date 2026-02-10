import os
import time
import cv2
import redis
import threading
import uuid
import json
import logging

# Set RTSP transport to TCP
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
RTSP_URLS = os.getenv("RTSP_URLS", "").split(",")
QUEUE_NAME = "video_stream_queue"
BUFFER_DURATION = float(os.getenv("BUFFER_DURATION", "10"))  # seconds
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

def cleanup_old_files(directory, retention_seconds):
    """
    Background thread to delete files older than retention_seconds.
    """
    logger.info(f"Retention policy started: keeping files for {retention_seconds}s")
    while True:
        try:
            now = time.time()
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if not filename.endswith(".mp4"):
                    continue
                    
                # Try to parse timestamp from filename first
                # Format: {stream_id}_{timestamp}_{duration}.mp4
                try:
                    parts = filename.replace(".mp4", "").split("_")
                    # Last part is duration, second to last is timestamp? 
                    # Naming: cam0_1700000000_2.0.mp4
                    # parts: ['cam0', '1700000000', '2.0']
                    # Be careful if stream_id has underscores.
                    # Best to assume last two parts are timestamp and duration.
                    if len(parts) >= 3:
                        file_ts = float(parts[-2])
                        if now - file_ts > retention_seconds:
                            os.remove(file_path)
                            # logger.debug(f"Deleted old file: {filename}")
                            continue
                except Exception:
                    # Fallback to file mtime
                    pass
                
                # Mtime fallback
                if os.path.isfile(file_path):
                    mtime = os.path.getmtime(file_path)
                    if now - mtime > retention_seconds:
                        os.remove(file_path)
                        # logger.debug(f"Deleted old file (mtime): {filename}")
                        
        except Exception as e:
            logger.error(f"Error in cleanup thread: {e}")
        
        time.sleep(10)

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
    # New Naming: {stream_id}_{timestamp}_{duration}.mp4
    # But we don't know duration yet! 
    # Logic: Write to a temp name, then rename on close.
    temp_filename = f"{stream_id}_recording_{uuid.uuid4()}.mp4"
    temp_file_path = os.path.join(TEMP_VIDEO_DIR, temp_filename)
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
    out = cv2.VideoWriter(temp_file_path, fourcc, fps, (width, height))
    
    while True:
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"Failed to read frame from {stream_id}. Reconnecting...")
            
            # Close partial file and delete to avoid corruption
            if out:
                out.release()
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass
            
            cap.release()
            time.sleep(2)
            
            # Attempt to reconnect
            cap = cv2.VideoCapture(stream_url)
            # If cap isn't opened immediately, the next read() will fail and we loop again.
            
            # Reset chunk state
            start_time = time.time()
            frame_count = 0
            temp_filename = f"{stream_id}_recording_{uuid.uuid4()}.mp4"
            temp_file_path = os.path.join(TEMP_VIDEO_DIR, temp_filename)
            out = cv2.VideoWriter(temp_file_path, fourcc, fps, (width, height))
            continue
        
        # Write frame immediately
        out.write(frame)
        frame_count += 1
        
        current_time = time.time()
        elapsed = current_time - start_time
        
        if elapsed >= BUFFER_DURATION:
            # Finalize current chunk
            out.release()
            
            final_filename = f"{stream_id}_{start_time:.3f}_{elapsed:.2f}.mp4"
            final_file_path = os.path.join(TEMP_VIDEO_DIR, final_filename)
            
            if frame_count > 0:
                try:
                    # Rename to final format
                    os.rename(temp_file_path, final_file_path)
                    
                    # Send payload
                    payload = {
                        "stream_id": stream_id,
                        "timestamp": start_time,
                        "duration": elapsed,
                        "video_path": final_file_path
                    }
                    if redis_client:
                        redis_client.rpush(QUEUE_NAME, json.dumps(payload))
                        logger.info(f"Pushed {elapsed:.2f}s from {stream_id} to Redis. File: {final_filename}")
                except Exception as e:
                    logger.error(f"Error processing chunk: {e}")
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
            else:
                 # Empty chunk?
                 if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
            
            # Start next chunk
            start_time = time.time()
            frame_count = 0
            temp_filename = f"{stream_id}_recording_{uuid.uuid4()}.mp4"
            temp_file_path = os.path.join(TEMP_VIDEO_DIR, temp_filename)
            out = cv2.VideoWriter(temp_file_path, fourcc, fps, (width, height))
        
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
    rtsp_base_url = os.getenv("RTSP_BASE_URL") # e.g., "rtsp://service:8554/cam"
    
    # Start Retention Thread
    retention_thread = threading.Thread(target=cleanup_old_files, args=(TEMP_VIDEO_DIR, 600), daemon=True)
    retention_thread.start()

    # Heartbeat Thread
    def heartbeat_loop(r_client, stream_id):
        while True:
            try:
                # Set key: camera:status:{stream_id} = "online" (TTL 30s)
                if r_client:
                    key = f"camera:status:{stream_id}"
                    r_client.set(key, "online", ex=30)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
            time.sleep(10)
    
    try:
        # Expected hostname format: capture-worker-{index}
        # We expect index to start from 0 for StatefulSet ordinals usually, but let's check.
        # If StatefulSet name is "capture-worker", pods are "capture-worker-0", "capture-worker-1"...
        # The user's previous code assumed "capture-worker-1" -> ID 1.
        # Let's handle generic "capture-worker-{N}" format.
        
        parts = hostname.split("-")
        worker_id = int(parts[-1])
        
        target_url = None
        display_stream_id = None
        
        if rtsp_base_url:
            # RTSP BASE URL이 공통일 때 다이나믹하게 할당            
            cam_index = worker_id 
            target_url = f"{rtsp_base_url}{cam_index-1}/media.smp"
            display_stream_id = f"cam{cam_index-1}" # Custom ID for display
            logger.info(f"Worker {hostname} (ID: {worker_id}) assigned to Dynamic URL: {target_url} with display ID: {display_stream_id}")
            
        else:
            # RTSP URL 리스트로 직접 넣어주는 방식
            url_index = worker_id - 1
            if 0 <= url_index < len(RTSP_URLS):
                target_url = RTSP_URLS[url_index]
                display_stream_id = f"cam{url_index}" # Custom ID for display, consistent with dynamic mode
                logger.info(f"Worker {hostname} (ID: {worker_id}) assigned to Legacy List Index: {url_index} with display ID: {display_stream_id}")
            else:
                 logger.error(f"Worker ID {worker_id} is out of range for {len(RTSP_URLS)} RTSP URLs.")

        if target_url and display_stream_id:
             # Start heartbeat
             hb_thread = threading.Thread(target=heartbeat_loop, args=(redis_client, display_stream_id), daemon=True)
             hb_thread.start()

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
