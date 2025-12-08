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
        # Blocking pop
        # blpop returns (key, element)
        item = redis_client.blpop(QUEUE_NAME, timeout=0)
        if not item:
            continue
            
        _, data_json = item
        try:
            payload = json.loads(data_json)
            stream_id = payload.get("stream_id")
            video_b64 = payload.get("video_data_base64")
            
            if not video_b64:
                continue
            
            print(f"Processing chunk from {stream_id}...")
            
            # Write temp video file
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                tmp_file.write(base64.b64decode(video_b64))
                tmp_video_path = tmp_file.name
            
            # Prepare input
            conversation = create_conversation(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                videos=[tmp_video_path],
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
            
            # Generate
            outputs = llm.generate([llm_inputs], sampling_params=sampling_params)
            
            # Output
            output_text = outputs[0].outputs[0].text
            
            print("--- Analysis Result ---")
            print(f"Stream: {stream_id}")
            print(output_text)
            print("-----------------------")
            
            # Cleanup
            os.remove(tmp_video_path)
            
        except Exception as e:
            print(f"Error processing item: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()
