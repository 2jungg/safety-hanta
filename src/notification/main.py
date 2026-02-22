
import os
import redis
import json
import time
import logging

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
NOTIFICATION_QUEUE = "notification_queue"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("notification")

def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

import requests

def send_telegram_alert(message, video_path=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        logger.warning("Telegram credentials not found. Skipping Telegram alert.")
        return

    # 1. Send Text
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {
        "chat_id": chat_id,
        "text": message
    }
    
    try:
        requests.post(url_msg, json=payload_msg, timeout=5)
        logger.info("Telegram text sent.")
    except Exception as e:
        logger.error(f"Error sending Telegram text: {e}")

    # 2. Send Video (if exists)
    if video_path and os.path.exists(video_path):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        try:
            with open(video_path, 'rb') as video_file:
                files = {'video': video_file}
                data = {'chat_id': chat_id, 'caption': "🚨 위험 영상"}
                response = requests.post(url_vid, data=data, files=files, timeout=60)
                if response.status_code == 200:
                   logger.info("Telegram video sent successfully.")
                else:
                   logger.error(f"Failed to send video: {response.text}")
        except Exception as e:
             logger.error(f"Error sending Telegram video: {e}")

def send_notification(event):
    """
    Formats and sends the notification.
    """
    try:
        stream_id = event.get("stream_id")
        timestamp = event.get("timestamp")
        level = event.get("level")
        description = event.get("description")
        context_logs = event.get("context_logs", [])
        
        time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
        
        # Format Message
        message = f"""
[🚨 {level} 감지]
📍 카메라 번호: {stream_id}
⏰ 시간: {time_str}
⚠️ 감지된 위험: {description}
📝 영상 맥락:
"""
        for log in context_logs:
            message += f"- {log}\n"
            
        logger.info("\n" + "="*50 + message + "="*50 + "\n")
        
        # Send to Telegram
        video_clip = event.get("video_clip")
        send_telegram_alert(message, video_clip)
        
    except Exception as e:
        logger.error(f"Error formatting notification: {e}")

def wait_for_redis(r):
    while True:
        try:
            if r.ping():
                logger.info("Connected to Redis.")
                break
        except redis.ConnectionError:
            logger.info("Waiting for Redis...")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Redis connection error: {e}")
            time.sleep(2)

def main():
    logger.info("Notification Service Started")
    r = get_redis_client()
    wait_for_redis(r)
    
    while True:
        try:
            # Blocking Pop
            item = r.blpop(NOTIFICATION_QUEUE, timeout=5)
            if item:
                queue, data = item
                event = json.loads(data)
                send_notification(event)
                
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
