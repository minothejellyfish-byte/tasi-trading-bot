#!/usr/bin/env python3
"""
Monitor the Derayah browser tab until login completes.
Captures auth token from localStorage and saves it.
Runs as daemon — survives parent shell exit.
"""
import os, sys, time, json, requests

if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)

sys.stdout = open('/tmp/login_monitor.log', 'w', buffering=1)
sys.stderr = sys.stdout

CDP_BASE = "http://127.0.0.1:18801"
TOKEN_FILE = "/home/mino/tasi-exec/derayah_token_live.json"
DONE_FILE  = "/tmp/login_monitor_done"

def cdp_get(path):
    try:
        return requests.get(f"{CDP_BASE}{path}", timeout=5).json()
    except Exception as e:
        print(f"CDP GET error: {e}")
        return None

def find_main_tab():
    tabs = cdp_get("/json") or []
    return next((t for t in tabs if "derayah.com" in t.get("url","") and t.get("type") == "page"), None)

def evaluate(ws_url, js):
    import websocket as ws_lib
    try:
        conn = ws_lib.create_connection(ws_url, timeout=10, header={"Origin": "http://localhost"})
        conn.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}))
        for _ in range(30):
            r = json.loads(conn.recv())
            if r.get("id") == 1:
                conn.close()
                return (r.get("result", {}) or {}).get("result", {}).get("value")
        conn.close()
    except Exception as e:
        print(f"WS eval error: {e}")
    return None

print(f"[{time.strftime('%H:%M:%S')}] Login monitor started")
start = time.time()
max_wait = 900  # 15 minutes

while time.time() - start < max_wait:
    tab = find_main_tab()
    if not tab:
        print(f"[{time.strftime('%H:%M:%S')}] No Derayah tab found, waiting...")
        time.sleep(10)
        continue

    url = tab.get("url", "")
    ws  = tab.get("webSocketDebuggerUrl", "")

    # Check if we're past the login page
    if "newonline.derayah.com" in url:
        print(f"[{time.strftime('%H:%M:%S')}] Dashboard detected! Capturing tokens...")
        # Grab all relevant localStorage keys
        token_data = {}
        for key in ["Derayah_accesstoken", "Derayah_refreshtoken", "TC_DERAYAH", "derayah_token"]:
            val = evaluate(ws, f"localStorage.getItem('{key}')")
            if val:
                token_data[key] = val
                print(f"  {key}: {str(val)[:60]}...")

        # Also grab cookies via document.cookie
        cookies = evaluate(ws, "document.cookie")
        if cookies:
            token_data["cookies"] = cookies
            print(f"  cookies: {str(cookies)[:100]}...")

        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f, indent=2)
        with open(DONE_FILE, "w") as f:
            f.write(f"login_ok\n{json.dumps(token_data)}\n")
        print(f"[{time.strftime('%H:%M:%S')}] Saved to {TOKEN_FILE} — login complete!")
        break

    elif "otp" in url.lower() or ("onboarding" in url and "signin" not in url):
        print(f"[{time.strftime('%H:%M:%S')}] OTP screen detected: {url}")

    else:
        print(f"[{time.strftime('%H:%M:%S')}] Still on login page, waiting...")

    time.sleep(5)

print(f"[{time.strftime('%H:%M:%S')}] Monitor done.")
