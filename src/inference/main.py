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
from rich import print
from rich.pretty import pprint

# Set up path to import project utils
# Assuming this script is at src/inference/main.py, we need to add project root
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
MAX_BATCH_SIZE = 16

# Reuse the analyze_videos_batch.py logic for setup
def setup_model():
    print(f"Loading Vision Config from {CONFIG_DIR}/vision_config.yaml")
    vision_kwargs = yaml.safe_load(open(CONFIG_DIR / "vision_config.yaml", "rb"))
    
    print(f"Loading Sampling Params from {CONFIG_DIR}/sampling_params.yaml")
    sampling_kwargs = yaml.safe_load(open(CONFIG_DIR / "sampling_params.yaml", "rb"))
    sampling_params = vllm.SamplingParams(**sampling_kwargs)
    
    print(f"Loading Prompt Config from {PROMPTS_DIR}/industrial_safety_report.yaml")
    prompt_kwargs = yaml.safe_load(open(PROMPTS_DIR / "industrial_safety_report.yaml", "rb"))
    prompt_config = PromptConfig.model_validate(prompt_kwargs)
    
    # System Prompt construction
    system_prompts = [open(f"{project_root}/prompts/addons/english.txt").read()]
    if prompt_config.system_prompt:
        system_prompts.append(prompt_config.system_prompt)
    
    # Check reasoning
    # Assuming enable_reasoning=True as per batch script default
    if "<think>" not in prompt_config.system_prompt:
         system_prompts.append(open(f"{project_root}/prompts/addons/reasoning.txt").read())
    
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
    
    print("Inference Worker Ready. Waiting for videos...")
    
    while True:
        # 1. Blocking pop for the first item
        item = redis_client.blpop(QUEUE_NAME, timeout=0)
        if not item:
            continue
            
        # blpop returns (key, value)
        batch = [item[1]]
        
        # 2. Dynamic batching: fetch remaining items up to limit
        while len(batch) < MAX_BATCH_SIZE:
            next_item = redis_client.lpop(QUEUE_NAME)
            if not next_item:
                break
            batch.append(next_item)
            
        print(f"Processing batch of {len(batch)} videos...")
        
        # Prepare batch inputs
        llm_inputs_batch = []
        original_payloads = []
        temp_files = []
        
        try:
            for data_json in batch:
                try:
                    payload = json.loads(data_json)
                    stream_id = payload.get("stream_id")
                    # video_b64 = payload.get("video_data_base64")
                    video_path = payload.get("video_path")
                    timestamp = payload.get("timestamp")
                    duration = payload.get("duration", 0)
                    
                    # 3. Load Shedding: Check latency
                    current_time = time.time()
                    latency = current_time - timestamp
                    if latency > 60.0:
                        print(f"Dropping stale message from {stream_id} (Latency: {latency:.2f}s > 60s)")
                        # Cleanup file if exists
                        if video_path and os.path.exists(video_path):
                            try:
                                os.remove(video_path)
                            except Exception as e:
                                print(f"Error deleting stale file {video_path}: {e}")
                        continue
                        
                    if not video_path or not os.path.exists(video_path):
                        print(f"Video file missing for {stream_id}: {video_path}")
                        continue
                        
                    # Calculate time range
                    start_dt = datetime.datetime.fromtimestamp(timestamp)
                    end_dt = datetime.datetime.fromtimestamp(timestamp + duration)
                    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
                    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Store file path for cleanup later
                    temp_files.append(video_path)
                    
                    # Inject Metadata into Prompt
                    # Format: [Camera Source: <id> | Time: <start> ~ <end>]
                    current_user_prompt = f"[Camera Source: {stream_id} | Time: {start_str} ~ {end_str}]\n{user_prompt}"
                    
                    conversation = create_conversation(
                        system_prompt=system_prompt,
                        user_prompt=current_user_prompt,
                        videos=[video_path],
                        vision_kwargs=vision_kwargs,
                    )
                    
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
                    print(f"Error preparing item in batch: {e}")
                    continue
            
            if not llm_inputs_batch:
                continue
                
            # Generate for batch
            outputs = llm.generate(llm_inputs_batch, sampling_params=sampling_params)
            
            # Process outputs
            for i, output in enumerate(outputs):
                output_text = output.outputs[0].text
                stream_id = original_payloads[i].get("stream_id")
                
                print("--- Analysis Result ---")
                print(f"Stream: {stream_id}")
                print(output_text)
                print("-----------------------")
                
        finally:
            # Cleanup all temp files
            for p in temp_files:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception as e:
                    print(f"Error cleaning up {p}: {e}")

if __name__ == "__main__":
    main()
