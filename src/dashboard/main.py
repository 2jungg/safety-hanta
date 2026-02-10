
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import redis
import json
import uvicorn
import cv2
import threading
import os

app = FastAPI()

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
ALERT_HISTORY_KEY = "Alert_History"
VIDEO_DIR = "/videos"
ACCIDENT_DIR = "/videos/accident_clips"

# RTSP Config (Must match capture worker)
RTSP_BASE_URL = os.getenv("RTSP_BASE_URL", "rtsp://admin:hankook2580@172.18.0.1:8558/LiveChannel/")

# Templates
templates = Jinja2Templates(directory="src/dashboard/templates")

def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

# Static Files (CSS, JS)
app.mount("/static", StaticFiles(directory="src/dashboard/static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/status")
async def get_status():
    """
    Returns list of online cameras based on heartbeat keys.
    """
    try:
        r = get_redis_client()
        # Scan for camera:status:*
        keys = r.keys("camera:status:*")
        online_cameras = []
        for key in keys:
            # key: camera:status:cam1
            stream_id = key.split(":")[-1]
            online_cameras.append(stream_id)
        
        return {"online": sorted(online_cameras)}
    except Exception as e:
        return {"error": str(e), "online": []}


def gen_frames(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url)
    # Be robust
    if not cap.isOpened():
        # Fallback to a placeholder or error?
        # Maybe retry or yield a "No Signal" frame logic?
        # For now simple error handling
        return

    while True:
        success, frame = cap.read()
        if not success:
            break
        
        # Optimization: Resize for web if needed?
        # frame = cv2.resize(frame, (640, 360))
        
        ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    
    cap.release()

@app.get("/api/live/{stream_id}")
async def get_live_stream(stream_id: str):
    """
    Proxies RTSP stream to MJPEG for browser.
    Assume stream_id format 'cam{N}' where N is 0-indexed index.
    URL logic: RTSP_BASE_URL + {N+1} + /media.smp (Based on capture worker logic)
    Wait, logic was: cam_index = worker_id. cam0 -> worker 1.
    So stream_id 'cam0' -> index 1. 'cam1' -> index 2.
    """
    try:
        # Parse index from cam{N}
        if stream_id.startswith("cam"):
            idx = int(stream_id.replace("cam", ""))
            target_index = idx  # FIX: MATCH CAPTURE WORKER LOGIC (cam0 -> 0)
            rtsp_url = f"{RTSP_BASE_URL}{target_index}/media.smp"
            
            # Use os.environ for Transport if needed
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            
            return StreamingResponse(gen_frames(rtsp_url), media_type="multipart/x-mixed-replace; boundary=frame")
        else:
            raise HTTPException(status_code=400, detail="Invalid stream ID format")
            
    except Exception as e:
        print(f"Stream error: {e}")
        raise HTTPException(status_code=500, detail="Stream failed")

@app.get("/api/events")
async def get_events():
    # ... existing code ...
    try:
        r = get_redis_client()
        # Get last 50 events
        events_json = r.lrange(ALERT_HISTORY_KEY, 0, 50)
        events = [json.loads(e) for e in events_json]
        return events
    except Exception as e:
        return {"error": str(e)}

@app.get("/video/{filename}")
async def get_video(filename: str):
    # ... existing code ...
    # Check accident dir first
    path = os.path.join(ACCIDENT_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    
    # Check general video dir (for temp files if needed)
    # Security: basic traversal check
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
        
    path = os.path.join(VIDEO_DIR, "temp_video", filename) # Assuming temp_video is where captures are
    if os.path.exists(path):
        return FileResponse(path)
        
    raise HTTPException(status_code=404, detail="Video not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
