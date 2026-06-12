#!/usr/bin/env python3
"""
TASI Coding Assistant - Consult Qwen3-Coder for code issues
Usage: python3 /home/mino/tasi-exec/consult_coder.py <issue_description>
"""

import sys
import subprocess
import json
import os

CODER_MODEL = "qwen3-coder:480b-cloud"

def consult_coder(issue: str, file_path: str = None):
    """Consult Qwen3-Coder for a coding issue."""
    
    # Build context
    context = f"""You are TASI's coding assistant. Review this issue and provide code fixes.

Issue: {issue}

"""
    
    if file_path and os.path.exists(file_path):
        with open(file_path) as f:
            code = f.read()
        context += f"\nFile: {file_path}\n```python\n{code[:5000]}\n```\n"
    
    context += """
Provide:
1. Root cause analysis
2. Specific fix with line numbers
3. Complete corrected code block
4. Testing recommendations
"""
    
    # Run ollama with coder model
    cmd = ["ollama", "run", CODER_MODEL, context]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    
    if result.returncode == 0:
        print("=== CODER ANALYSIS ===")
        print(result.stdout)
        return result.stdout
    else:
        print(f"Error: {result.stderr}")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 consult_coder.py '<issue description>' [file_path]")
        sys.exit(1)
    
    issue = sys.argv[1]
    file_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    consult_coder(issue, file_path)
