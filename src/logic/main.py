
import os
import redis
import json
import time
import subprocess
import logging
import datetime
import re
import glob

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
INPUT_STREAM_KEY = "vlm_inference_stream"
NOTIFICATION_QUEUE = "notification_queue"
ALERT_HISTORY_KEY = "Alert_History"
VIDEO_DIR = "/videos/temp_video"
ACCIDENT_DIR = "/videos/accident_clips"

# Ensure accident dir exists
os.makedirs(ACCIDENT_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("logic")

def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

def parse_safety_status(text):
    """
    Parses 'Safety Status: [Safe/Warn/Danger/Extreme]' from text.
    Returns status string (uppercase) or None.
    """
    match = re.search(r"Safety Status:\s*(Safe|Warn|Danger|Extreme)", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return "UNKNOWN"

def find_video_files(stream_id, start_ts, end_ts):
    """
    Finds .mp4 files that overlap with the requested time range.
    Naming convention: {stream_id}_{start_time}_{duration}.mp4
    """
    relevant_files = []
    try:
        # Optimization: use glob to filter by stream_id
        # pattern = os.path.join(VIDEO_DIR, f"{stream_id}_*.mp4")
        # files = glob.glob(pattern)
        # However, listing all files might be slow if many. 
        # But rolling retention keeps it reasonable (10 mins = 60 files * streams).
        
        files = os.listdir(VIDEO_DIR)
        for f in files:
            if not f.startswith(stream_id) or not f.endswith(".mp4"):
                continue
                
            try:
                parts = f.replace(".mp4", "").split("_")
                # Expected: cam0_1705673130_2.0.mp4
                if len(parts) >= 3:
                    file_start = float(parts[-2])
                    duration = float(parts[-1])
                    file_end = file_start + duration
                    
                    # Check overlap
                    if file_end > start_ts and file_start < end_ts:
                        relevant_files.append((file_start, os.path.join(VIDEO_DIR, f)))
            except:
                continue
                
        # Sort by time
        relevant_files.sort(key=lambda x: x[0])
        return [x[1] for x in relevant_files]
        
    except Exception as e:
        logger.error(f"Error finding files: {e}")
        return []

def create_accident_clip(stream_id, event_ts):
    """
    Creates a clip from T-5s to T+5s.
    """
    start_ts = event_ts - 5
    end_ts = event_ts + 5
    
    files = find_video_files(stream_id, start_ts, end_ts)
    if not files:
        logger.warning(f"No video files found for event at {event_ts}")
        return None
        
    # Output filename
    timestamp_str = datetime.datetime.fromtimestamp(event_ts).strftime("%Y%m%d_%H%M%S")
    output_filename = f"{stream_id}_{timestamp_str}_ACCIDENT.mp4"
    output_path = os.path.join(ACCIDENT_DIR, output_filename)
    
    # FFmpeg logic
    # If 1 file, simple cut.
    # If multiple, contact first?
    # Complex stitching might be slow. 
    # Quick fix: If multiple files, just use concatenation protocol.
    
    try:
        # Create input list for ffmpeg
        with open("input_list.txt", "w") as f:
            for video in files:
                f.write(f"file '{video}'\n")
        
        # We need to calculate start offset relative to the first file's start time
        # taking into account that the first file might start before start_ts.
        # Actually, simpler to just Concat ALL then Cut?
        # Creating a temp concat file.
        
        temp_concat = f"temp_concat_{stream_id}_{event_ts}.mp4"
        
        # 1. Concat (Re-muxing, fast)
        subprocess.run([
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", "input_list.txt", 
            "-c", "copy", "-y", temp_concat
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 2. Cut relevant section
        # We need the start time of the first file to know offset
        first_file_name = os.path.basename(files[0])
        # cam0_1705673130_2.0.mp4
        first_parts = first_file_name.replace(".mp4", "").split("_")
        first_file_start = float(first_parts[-2])
        
        seek_start = start_ts - first_file_start
        if seek_start < 0: seek_start = 0
        
        duration = end_ts - start_ts # 10s
        
        subprocess.run([
            "ffmpeg", "-ss", str(seek_start), "-i", temp_concat, "-t", str(duration),
            "-c", "copy", "-y", output_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Cleanup
        if os.path.exists(temp_concat):
            os.remove(temp_concat)
        if os.path.exists("input_list.txt"):
            os.remove("input_list.txt")
            
        logger.info(f"Created accident clip: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"FFmpeg failed: {e}")
        return None

def main():
    logger.info("Logic Service Started")
    r = get_redis_client()
    
    # Create consumer group if not exists
    group_name = "logic_group"
    consumer_name = os.getenv("HOSTNAME", "logic-consumer-1")
    
    try:
        r.xgroup_create(INPUT_STREAM_KEY, group_name, id="0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass # Group exists
        
    while True:
        try:
            # Read from Stream
            entries = r.xreadgroup(group_name, consumer_name, {INPUT_STREAM_KEY: ">"}, count=1, block=2000)
            
            if not entries:
                continue
                
            for stream, messages in entries:
                for message_id, data in messages:
                    try:
                        # Decode data
                        payload = {k.decode("utf-8"): v.decode("utf-8") if isinstance(v, bytes) else v for k, v in data.items()}
                        vlm_output = payload.get("vlm_output", "")
                        stream_id = payload.get("stream_id")
                        timestamp = float(payload.get("timestamp", 0))
                        
                        # 1. Parse Status
                        status = parse_safety_status(vlm_output)
                        logger.info(f"Stream {stream_id} Status: {status}")
                        
                        # 2. Store History (for Context)
                        # We store brief summary: "Time: Status - Hazard"
                        # Extract hazard?
                        # Let's just store the full VLM output or a summary.
                        # Format: "{time_str}: {vlm_output_first_line}..."
                        time_str = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                        
                        # Extract hazard line
                        hazard_match = re.search(r"Identified Hazard:.*", vlm_output)
                        hazard_summary = hazard_match.group(0) if hazard_match else "Status: " + status
                        
                        log_entry = f"{time_str}: {hazard_summary}"
                        
                        history_key = f"channel_history:{stream_id}"
                        r.lpush(history_key, log_entry)
                        r.ltrim(history_key, 0, 10) # Keep last 10 entries
                        
                        # 3. Danger Handling
                        if status in ["DANGER", "EXTREME"]:
                            logger.info(f"🚨 DANGER/EXTREME detected on {stream_id}!")
                            
                            # Create Clip
                            clip_path = create_accident_clip(stream_id, timestamp)
                            
                            # Construct Event
                            event = {
                                "type": "ALERT",
                                "level": status,
                                "stream_id": stream_id,
                                "timestamp": timestamp,
                                "description": hazard_summary,
                                "video_clip": clip_path,
                                "full_analysis": vlm_output,
                                "context_logs": [x.decode('utf-8') for x in r.lrange(history_key, 0, 5)]
                            }
                            
                            event_json = json.dumps(event)
                            
                            # Publish to Notification Queue
                            r.rpush(NOTIFICATION_QUEUE, event_json)
                            
                            # Publish to Alert History (for Dashboard)
                            r.lpush(ALERT_HISTORY_KEY, event_json)
                            r.ltrim(ALERT_HISTORY_KEY, 0, 50)
                            
                        # Ack message
                        r.xack(INPUT_STREAM_KEY, group_name, message_id)
                        
                    except Exception as e:
                        logger.error(f"Error processing message {message_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
