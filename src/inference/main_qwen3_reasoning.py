import sys
import os

# # Fix for vLLM memory profiling error in some environments
# os.environ["VLLM_TEST_FORCE_FP32_MATCH"] = "0"

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

import torch

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
QUEUE_NAME = "video_stream_queue"
EXCLUDED_STREAMS = []
MODEL_PATH = os.getenv("MODEL_PATH", str(project_root / "models/Qwen3-VL-2B-Instruct-NVFP4"))
# SPECULATIVE_MODEL_PATH = os.getenv("SPECULATIVE_MODEL_PATH", str(project_root / "models/Qwen3-VL-2B-Instruct-NVFP4"))
CONFIG_DIR = project_root / "configs"
PROMPTS_DIR = project_root / "prompts"
MAX_BATCH_SIZE = 20
MIN_BATCH_SIZE = int(os.getenv("MIN_BATCH_SIZE", 1))
BATCH_TIMEOUT = float(os.getenv("BATCH_TIMEOUT", 1.0))

# Pipeline Configuration
PREPARED_QUEUE_SIZE = 1

def setup_model():
    print(f"Loading Vision Config from {CONFIG_DIR}/vision_config.yaml")
    vision_kwargs = yaml.safe_load(open(CONFIG_DIR / "vision_config.yaml", "rb"))
    
    print(f"Loading Sampling Params from {CONFIG_DIR}/sampling_params.yaml")
    sampling_kwargs = yaml.safe_load(open(CONFIG_DIR / "sampling_params.yaml", "rb"))
    sampling_params = vllm.SamplingParams(**sampling_kwargs)
    
    print(f"Loading Prompt Config from {PROMPTS_DIR}/industrial_safety_reasoning.yaml")
    prompt_kwargs = yaml.safe_load(open(PROMPTS_DIR / "industrial_safety_reasoning.yaml", "rb"))
    prompt_config = PromptConfig.model_validate(prompt_kwargs)
    
    # System Prompt construction
    system_prompts = [open(f"{project_root}/prompts/addons/english.txt").read()]
    if prompt_config.system_prompt:
        system_prompts.append(prompt_config.system_prompt)
    
    system_prompt = "\n\n".join(map(str.rstrip, system_prompts))
    user_prompt = prompt_config.user_prompt
    if not user_prompt:
        raise ValueError("No user prompt provided.")
    
    print(f"Loading Model from {MODEL_PATH}...")
    try:
        llm = vllm.LLM(
            model=MODEL_PATH,
            limit_mm_per_prompt={"video": 1},
            enable_prefix_caching=False,
            gpu_memory_utilization=float(os.getenv("GPU_MEMORY_UTILIZATION", 0.6)),
            trust_remote_code=True,
            max_model_len=int(os.getenv("MAX_MODEL_LEN", 262144)),
        )
    except Exception as e:
        print(f"Error loading model: {e}")
        raise
    
    # Use generic AutoProcessor for Qwen3 compatibility
    processor = transformers.AutoProcessor.from_pretrained(MODEL_PATH)
    
    return llm, processor, sampling_params, vision_kwargs, system_prompt, user_prompt

def pin_memory_recursive(obj):
    """
    Recursively pin tensors in memory.
    This enables faster Host-to-Device transfer (or Zero-Copy on Unified Memory).
    """
    if torch.is_tensor(obj):
        if obj.device.type == 'cpu' and not obj.is_pinned():
            return obj.pin_memory()
        return obj
    elif isinstance(obj, dict):
        return {k: pin_memory_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [pin_memory_recursive(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(pin_memory_recursive(v) for v in obj)
    else:
        return obj

def batch_preparer_worker(redis_client, processor, vision_kwargs, system_prompt, user_prompt, output_queue):
    """
    Producer thread:
    1. Fetches data from Redis.
    2. Decodes video and prepares model inputs (CPU intensive).
    3. Puts ready batches into output_queue.
    """
    print("Batch Preparer Thread Started.")
    
    # Helper for hashing dicts (vLLM workaround)
    class HashableDict(dict):
        def __hash__(self):
            return hash(tuple(sorted(self.items())))
    
    def make_hashable(obj):
        if isinstance(obj, dict):
            return HashableDict({k: make_hashable(v) for k, v in obj.items()})
        elif isinstance(obj, list):
            return tuple(make_hashable(i) for i in obj)
        return obj

    while True:
        # Prepare batch of valid items
        batch_data = []
        start_wait_time = None
        
        # [OPTIMIZATION] Backlog Clearing (Freshness First)
        # If queue is too long, we drop old items to process only the latest.
        try:
            q_len = redis_client.llen(QUEUE_NAME)
            if q_len > MAX_BATCH_SIZE * 2:
                # Keep only the last MAX_BATCH_SIZE items (Newest)
                # Redis List: [Oldest, ..., Newest]
                # LTRIM key -N -1 keeps the tail.
                redis_client.ltrim(QUEUE_NAME, -MAX_BATCH_SIZE, -1)
                print(f"[Preparer] Queue backlog ({q_len}) detected. Trimmed to latest {MAX_BATCH_SIZE}.")
        except Exception as e:
            print(f"Error checking/trimming queue: {e}")

        # 1. Fetch Loop
        while len(batch_data) < MAX_BATCH_SIZE:
            if len(batch_data) == 0:
                # Blocking pop for first item to avoid busy wait
                item = redis_client.blpop(QUEUE_NAME, timeout=1) 
                if not item:
                    continue # Try again
                data_json = item[1]
                start_wait_time = time.time() # Start timeout timer after first item
            else:
                # Non-blocking pop for subsequent items
                data_json = redis_client.lpop(QUEUE_NAME)
                
                if not data_json:
                    # Check if we should wait more or break
                    if len(batch_data) < MIN_BATCH_SIZE:
                        # Check timeout
                        if time.time() - start_wait_time > BATCH_TIMEOUT:
                            break
                        time.sleep(0.01) # Short sleep to avoid busy wait
                        continue
                    else:
                        break
            
            # 2. Validation & Filtering Loop
            try:
                payload = json.loads(data_json)
                stream_id = payload.get("stream_id")
                
                # Check if stream is excluded
                if stream_id in EXCLUDED_STREAMS:
                    # properly clean up if needed, or just continue
                    video_path = payload.get("video_path")
                    if video_path and os.path.exists(video_path):
                        try:
                            # We can remove it immediately if we're sure we don't want it,
                            # to save disk space and avoid it being picked up again (though it won't be picked up again as it's popped from queue)
                            # But deletion is handled by capture service retention usually.
                            # However, to avoid accumulation if capture service is slow, let's delete it.
                            os.remove(video_path)
                            print(f"[Preparer] Skipped excluded stream: {stream_id}")
                        except Exception:
                            pass
                    continue

                video_path = payload.get("video_path")
                timestamp = payload.get("timestamp")
                
                # Check latency
                current_time = time.time()
                latency = current_time - timestamp
                
                if latency > 60.0:
                    # Drop stale message
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
        batch_process_start = time.time()
        
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
                    
                    # Store file path for passthrough (Deletion handled by Capture Service now)
                    temp_files.append(video_path)
                    
                    # ---------------------------------------------------------
                    # [NEW] Context Injection from Redis
                    # ---------------------------------------------------------
                    context_history = ""
                    try:
                        # Fetch last 3 summaries from Channel_History
                        # Key: channel_history:{stream_id}
                        history_key = f"channel_history:{stream_id}"
                        recent_logs = redis_client.lrange(history_key, 0, 2)
                        if recent_logs:
                            context_lines = []
                            for log_bytes in reversed(recent_logs): # Oldest first
                                if isinstance(log_bytes, bytes):
                                    log_str = log_bytes.decode('utf-8')
                                    context_lines.append(log_str)
                            
                            if context_lines:
                                context_history = "\n[Recent Context]\n" + "\n".join(context_lines) + "\n"
                    except Exception as e:
                        print(f"Failed to fetch context: {e}")
                    
                    # Inject Context into User Prompt (Clarified)
                    # We explicitly tell the model that context is historical and it must focus on the CURRENT video.
                    current_user_prompt = f"""
                                            Analyze the provided video clip.
                                            {user_prompt}
                                           """
                    conversation = create_conversation(
                        system_prompt=system_prompt,
                        user_prompt=current_user_prompt,
                        videos=[video_path],
                        vision_kwargs=vision_kwargs,
                    )
                    
                    start_process = time.time()
                    # Tokenization and Vision Processing (CPU)
                    prompt = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
                    
                    # Qwen3 specific handling
                    image_patch_size = processor.image_processor.patch_size if hasattr(processor, "image_processor") else 14
                    
                    _image_inputs, video_inputs, video_kwargs = qwen_vl_utils.process_vision_info(
                        conversation, 
                        return_video_kwargs=True, 
                        return_video_metadata=True,
                        image_patch_size=image_patch_size
                    )
                    
                    # Optimization: Pin memory to speed up transfer (Unified Memory optimization)
                    if video_inputs is not None:
                        video_inputs = pin_memory_recursive(video_inputs)

                    # Apply workaround to video_kwargs
                    if video_kwargs:
                        video_kwargs = make_hashable(video_kwargs)
                    
                    mm_data = {}
                    if _image_inputs is not None:
                        mm_data['image'] = _image_inputs
                    if video_inputs is not None:
                        mm_data['video'] = video_inputs
                    
                    llm_inputs = {
                        "prompt": prompt,
                        "multi_modal_data": mm_data,
                        "mm_processor_kwargs": video_kwargs,
                    }
                    
                    llm_inputs_batch.append(llm_inputs)
                    original_payloads.append(payload)
                    
                except Exception as e:
                    print(f"[Preparer] Error preparing item in batch: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            if llm_inputs_batch:
                # Put ready batch into queue
                output_queue.put((llm_inputs_batch, original_payloads, temp_files))
                print(f"[Preparer] Batch enqueued. Queue size: {output_queue.qsize()}")
            else:
                 pass
                    
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
    
    # Redis for Output
    output_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    OUTPUT_STREAM_KEY = "vlm_inference_stream"

    while True:
        # Get ready batch from queue
        try:
            # Blocking get
            llm_inputs_batch, original_payloads, temp_files = batch_queue.get()
            
            print(f"[Main] Processing batch of {len(llm_inputs_batch)} on GPU...")
            
            # Generate (GPU)
            gen_start = time.time()
            outputs = llm.generate(llm_inputs_batch, sampling_params=sampling_params)
            print(f"[Main] GPU Inference time: {time.time() - gen_start:.4f}s")
            
            # Process outputs
            for i, output in enumerate(outputs):
                output_text = output.outputs[0].text
                input_payload = original_payloads[i]
                stream_id = input_payload.get("stream_id")
                timestamp = input_payload.get("timestamp")
                video_path = input_payload.get("video_path")
                
                # [NEW] Parse Reasoning & Answer
                # format: <think>...</think><answer>...</answer>
                extracted, _ = extract_tagged_text(output_text)
                
                # Extract first occurrence of each tag
                reasoning_list = extracted.get("think", [])
                reasoning = reasoning_list[0].strip() if reasoning_list else ""
                
                answer_list = extracted.get("answer", [])
                final_answer = answer_list[0].strip() if answer_list else ""
                
                # Fallback if tags are missing (legacy support or failure)
                if not final_answer:
                    final_answer = output_text

                print("--- Analysis Result ---")
                print(f"Stream: {stream_id}")
                print(f"[Reasoning]: {reasoning[:100]}...")
                print(f"[Answer]: {final_answer}")
                print("-----------------------")
                
                # Publish to Redis Stream
                try:
                    event_data = {
                        "stream_id": stream_id,
                        "timestamp": timestamp,
                        "vlm_output": final_answer,
                        "vlm_reasoning": reasoning,
                        "video_path": video_path,
                        "processed_at": time.time()
                    }
                    output_redis.xadd(OUTPUT_STREAM_KEY, event_data, maxlen=1000)
                except Exception as e:
                    print(f"Error publishing to Redis Stream: {e}")

            # Cleanup
            # [MODIFIED] Do NOT delete temp files here. Retention is handled by Capture Service.
            # print("[Main] Cleaning up temp files... (SKIPPED - Handled by Capture Service)")
                    
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Main] Error: {e}")

if __name__ == "__main__":
    main()
