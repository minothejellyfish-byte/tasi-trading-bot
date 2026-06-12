#!/usr/bin/env python3
"""
Remote ComfyUI Controller
Run ComfyUI on Amin-PC, control from Ocean
"""

import subprocess
import json
import time
import urllib.request
import urllib.parse

PC_HOST = "192.168.1.228"
COMFYUI_PATH = "C:\\Users\\Administrator\\Desktop\\Mino\\ComfyUI"
MODEL_PATH = f"{COMFYUI_PATH}\\models\\checkpoints\\v1-5-pruned.safetensors"

def run_on_pc(command):
    """Run command on Amin-PC via SSH"""
    ssh_cmd = ["ssh", "amin-pc", command]
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode

def check_model():
    """Check if model is downloaded"""
    stdout, _, _ = run_on_pc(
        f'powershell -Command "(Get-Item \"{MODEL_PATH}\").Length"'
    )
    try:
        size = int(stdout.strip())
        return size > 4_000_000_000  # ~4GB
    except:
        return False

def start_comfyui():
    """Start ComfyUI on Amin-PC"""
    if not check_model():
        return False, "Model not ready yet"
    
    # Start in background
    run_on_pc(
        f'start /B cmd /c "cd /d {COMFYUI_PATH} && python main.py --auto-launch"'
    )
    time.sleep(5)
    
    # Check if running
    stdout, _, _ = run_on_pc(
        'powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Select-Object Id"'
    )
    if stdout.strip():
        return True, "ComfyUI started"
    return False, "Failed to start"

def stop_comfyui():
    """Stop ComfyUI on Amin-PC"""
    run_on_pc(
        'powershell -Command "Get-Process python | Where-Object {$_.MainWindowTitle -like \"*ComfyUI*\"} | Stop-Process -Force"'
    )
    return True, "Stopped"

def check_status():
    """Check if ComfyUI is running"""
    stdout, _, _ = run_on_pc(
        'powershell -Command "try { \$r = Invoke-WebRequest -Uri \"http://127.0.0.1:8188/system_stats\" -UseBasicParsing -TimeoutSec 5; Write-Host \"RUNNING\" } catch { Write-Host \"NOT_RUNNING\" }"'
    )
    return "RUNNING" in stdout

def generate_image(prompt, width=512, height=512):
    """Generate image via ComfyUI API"""
    # Basic workflow for text2img
    workflow = {
        "3": {
            "inputs": {
                "seed": int(time.time()),
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "KSampler"
        },
        "4": {
            "inputs": {"ckpt_name": "v1-5-pruned.safetensors"},
            "class_type": "CheckpointLoaderSimple"
        },
        "5": {
            "inputs": {"width": width, "height": height, "batch_size": 1},
            "class_type": "EmptyLatentImage"
        },
        "6": {
            "inputs": {"text": prompt},
            "class_type": "CLIPTextEncode"
        },
        "7": {
            "inputs": {"text": "bad quality, blurry"},
            "class_type": "CLIPTextEncode"
        },
        "8": {
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            "class_type": "VAEDecode"
        },
        "9": {
            "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]},
            "class_type": "SaveImage"
        }
    }
    
    data = json.dumps({"prompt": workflow}).encode()
    req = urllib.request.Request(
        f"http://{PC_HOST}:8188/prompt",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        response = urllib.request.urlopen(req, timeout=30)
        return json.loads(response.read())
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python comfyui_remote.py [start|stop|status|generate <prompt>]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "start":
        ok, msg = start_comfyui()
        print(msg)
    elif cmd == "stop":
        ok, msg = stop_comfyui()
        print(msg)
    elif cmd == "status":
        if check_status():
            print("ComfyUI is RUNNING")
        else:
            print("ComfyUI is NOT RUNNING")
    elif cmd == "generate" and len(sys.argv) > 2:
        prompt = " ".join(sys.argv[2:])
        result = generate_image(prompt)
        print(json.dumps(result, indent=2))
    else:
        print("Unknown command")
