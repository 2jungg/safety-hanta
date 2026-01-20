
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import redis
import json
import os
import uvicorn

app = FastAPI()

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
ALERT_HISTORY_KEY = "Alert_History"
VIDEO_DIR = "/videos"
ACCIDENT_DIR = "/videos/accident_clips"

# Templates
templates = Jinja2Templates(directory="src/dashboard/templates")

def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/events")
async def get_events():
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
