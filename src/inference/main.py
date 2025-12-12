import sys
import os
import pathlib
import json
import base64
import tempfile
import time
import textwrap
import redis
import yaml
import collections
import datetime
import threading
import queue
from rich import print
from rich.pretty import pprint

# Set up path to import project utils
project_root = pathlib.Path(__file__).parents[2].resolve()
sys.path.append(str(project_root))

from cosmos_reason1_utils.script import init_script
init_script()

import qwen_vl_utils
import transformers
import vllm
from cosmos_reason1_utils.text import (
    PromptConfig,
    create_conversation,
    extract_tagged_text,
)
from cosmos_reason1_utils.vision import VisionConfig

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
QUEUE_NAME = "video_stream_queue"
MODEL_PATH = os.getenv("MODEL_PATH", str(project_root / "saved_models_Cosmos-Reason1-7B_nvfp4_hf"))
CONFIG_DIR = project_root / "configs"
PROMPTS_DIR = project_root / "prompts"
MAX_BATCH_SIZE = 20

# Pipeline Configuration
PREPARED_QUEUE_SIZE = 1

def setup_model():
    print(f"Loading Vision Config from {CONFIG_DIR}/vision_config.yaml")
    vision_kwargs = yaml.safe_load(open(CONFIG_DIR / "vision_config.yaml", "rb"))
    
    print(f"Loading Sampling Params from {CONFIG_DIR}/sampling_params.yaml")
    sampling_kwargs = yaml.safe_load(open(CONFIG_DIR / "sampling_params.yaml", "rb"))
    sampling_params = vllm.SamplingParams(**sampling_kwargs)
    
    print(f"Loading Prompt Config from {PROMPTS_DIR}/industrial_safety_short.yaml")
    prompt_kwargs = yaml.safe_load(open(PROMPTS_DIR / "industrial_safety_short.yaml", "rb"))
    prompt_config = PromptConfig.model_validate(prompt_kwargs)
    
    # System Prompt construction
    system_prompts = [open(f"{project_root}/prompts/addons/english.txt").read()]
    if prompt_config.system_prompt:
        system_prompts.append(prompt_config.system_prompt)
    
    system_prompt = "\n\n".join(map(str.rstrip, system_prompts))
    user_prompt = prompt_config.user_prompt
    if not user_prompt:
        raise ValueError("No user prompt provided.")
    
    print("Loading Model...")
    llm = vllm.LLM(
        model=MODEL_PATH,
        limit_mm_per_prompt={"video": 1},
        gpu_memory_utilization=0.5,
    )
    
    processor = transformers.AutoProcessor.from_pretrained(MODEL_PATH)
    
    return llm, processor, sampling_params, vision_kwargs, system_prompt, user_prompt

def batch_preparer_worker(redis_client, processor, vision_kwargs, system_prompt, user_prompt, output_queue):
    """
    Producer thread:
    1. Fetches data from Redis.
    2. Decodes video and prepares model inputs (CPU intensive).
    3. Puts ready batches into output_queue.
    """
    print("Batch Preparer Thread Started.")
    
    while True:
        # Prepare batch of valid items
        batch_data = []
        
        # 1. Fetch Loop
        while len(batch_data) < MAX_BATCH_SIZE:
            if len(batch_data) == 0:
                # Blocking pop for first item to avoid busy wait
                item = redis_client.blpop(QUEUE_NAME, timeout=1) 
                if not item:
                    continue # Try again
                data_json = item[1]
            else:
                # Non-blocking pop for subsequent items
                data_json = redis_client.lpop(QUEUE_NAME)
                if not data_json:
                    # If queue is empty but we have some items, check if we should wait or process
                    # For now, let's just break and process what we have to keep low latency
                    break 
            
            # 2. Validation & Filtering Loop
            try:
                payload = json.loads(data_json)
                stream_id = payload.get("stream_id")
                video_path = payload.get("video_path")
                timestamp = payload.get("timestamp")
                
                # Check latency
                current_time = time.time()
                latency = current_time - timestamp
                
                if latency > 60.0:
                    # Drop stale message
                    # print(f"Dropping stale message from {stream_id} (Latency: {latency:.2f}s > 60s)")
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                        except Exception as e:
                            print(f"Error deleting stale file {video_path}: {e}")
                    continue
                
                if not video_path or not os.path.exists(video_path):
                    print(f"Video file missing for {stream_id}: {video_path}")
                    continue
                    
                batch_data.append(payload)
                
            except Exception as e:
                print(f"Error parsing item: {e}")
                continue

        if not batch_data:
            continue
            
        print(f"[Preparer] Prepared batch of {len(batch_data)} videos. Processing inputs...")
        
        # 3. Processing Loop (Heavy CPU/IO)
        llm_inputs_batch = []
        original_payloads = []
        temp_files = []
        
        try:
            for payload in batch_data:
                try:
                    stream_id = payload.get("stream_id")
                    video_path = payload.get("video_path")
                    timestamp = payload.get("timestamp")
                    duration = payload.get("duration", 0)
                    
                    # Calculate time range
                    start_dt = datetime.datetime.fromtimestamp(timestamp)
                    end_dt = datetime.datetime.fromtimestamp(timestamp + duration)
                    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Store file path for cleanup later
                    temp_files.append(video_path)
                    
                    # Inject Metadata into Prompt
                    current_user_prompt = f"[Camera Source: {stream_id} | Time: {start_str} ~ {end_str}]\n{user_prompt}"
                    
                    conversation = create_conversation(
                        system_prompt=system_prompt,
                        user_prompt=current_user_prompt,
                        videos=[video_path],
                        vision_kwargs=vision_kwargs,
                    )
                    
                    # Tokenization and Vision Processing (CPU)
                    prompt = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
                    _image_inputs, video_inputs, video_kwargs = qwen_vl_utils.process_vision_info(conversation, return_video_kwargs=True)
                    
                    mm_data = {"video": video_inputs} if video_inputs is not None else {}
                    llm_inputs = {
                        "prompt": prompt,
                        "multi_modal_data": mm_data,
                        "mm_processor_kwargs": video_kwargs,
                    }
                    
                    llm_inputs_batch.append(llm_inputs)
                    original_payloads.append(payload)
                    
                except Exception as e:
                    print(f"[Preparer] Error preparing item in batch: {e}")
                    continue
            
            if llm_inputs_batch:
                # Put ready batch into queue
                # Structure: (llm_inputs, payloads, temp_files)
                output_queue.put((llm_inputs_batch, original_payloads, temp_files))
                print(f"[Preparer] Batch enqueued. Queue size: {output_queue.qsize()}")
            else:
                 # If all failed, clean up immediately
                 for p in temp_files:
                    try:
                        if os.path.exists(p): os.remove(p)
                    except: pass
                    
        except Exception as e:
            print(f"[Preparer] Critical Error: {e}")


def main():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    
    # Wait for Redis
    while True:
        try:
            if redis_client.ping():
                break
        except Exception:
            print("Waiting for Redis...")
            time.sleep(2)
            
    llm, processor, sampling_params, vision_kwargs, system_prompt, user_prompt = setup_model()
    
    # Setup Pipeline
    batch_queue = queue.Queue(maxsize=PREPARED_QUEUE_SIZE)
    
    # Start Producer Thread
    t = threading.Thread(
        target=batch_preparer_worker,
        args=(redis_client, processor, vision_kwargs, system_prompt, user_prompt, batch_queue)
    )
    t.daemon = True
    t.start()
    
    print("Inference Main Loop Started (Consuming Batches)...")
    
    while True:
        # Get ready batch from queue
        try:
            # Blocking get
            llm_inputs_batch, original_payloads, temp_files = batch_queue.get()
            
            print(f"[Main] Processing batch of {len(llm_inputs_batch)} on GPU...")
            
            # Generate (GPU)
            outputs = llm.generate(llm_inputs_batch, sampling_params=sampling_params)
            
            # Process outputs
            for i, output in enumerate(outputs):
                output_text = output.outputs[0].text
                stream_id = original_payloads[i].get("stream_id")
                
                print("--- Analysis Result ---")
                print(f"Stream: {stream_id}")
                print(output_text)
                print("-----------------------")
            
            # Cleanup
            print("[Main] Cleaning up temp files...")
            for p in temp_files:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception as e:
                    print(f"Error cleaning up {p}: {e}")
                    
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Main] Error: {e}")

if __name__ == "__main__":
    main()
