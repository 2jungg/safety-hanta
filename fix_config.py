import json
import os

config_path = "models/Qwen3-VL-2B-Instruct-NVFP4/config.json"

if not os.path.exists(config_path):
    print(f"Error: {config_path} not found")
    exit(1)

with open(config_path, "r") as f:
    config = json.load(f)

# Helper to recursively remove keys
def remove_keys(obj, keys_to_remove):
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in keys_to_remove:
                print(f"Removing key: {key}")
                del obj[key]
            else:
                remove_keys(obj[key], keys_to_remove)
    elif isinstance(obj, list):
        for item in obj:
            remove_keys(item, keys_to_remove)

remove_keys(config, ["scale_dtype", "zp_dtype"])

# Backup original
os.rename(config_path, config_path + ".bak")

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)

print("Modified config.json saved.")
