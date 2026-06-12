#!/usr/bin/env python3
"""
One-shot sniffer: listens on the login page for the Derayah auth POST.
Run this, then log in manually via CRD. The auth request is captured and saved.
"""
import asyncio, json, base64
from pathlib import Path
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:18801"
OUT = Path("/home/mino/tasi-exec/derayah_auth_request.json")

async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP, timeout=6000)
        ctx = browser.contexts[0]
        tab = next((p for p in ctx.pages if "derayah.com" in p.url), None)
        if not tab:
            print("No Derayah tab"); return
        print(f"Watching: {tab.url}")
        print("Log in manually via CRD now...")

        captured = []

        NOISE = ("nr-data.net", "doubleclick.net", "google.com", "linkedin.com",
                 "facebook.com", "tiktok", "snapchat", "infobip", "appdynamics",
                 "flagsmith", "analytics")

        async def on_request(req):
            url = req.url
            is_noise = any(n in url for n in NOISE)
            is_auth = ("connect/token" in url or
                       ("onboarding.derayah.com" in url and req.method == "POST") or
                       ("api.derayah.com" in url and req.method == "POST"))
            if is_auth and not is_noise:
                body = req.post_data or ""
                headers = dict(req.headers)
                entry = {"url": url, "method": req.method, "body": body, "headers": headers}
                captured.append(entry)
                print(f"\n*** AUTH REQUEST CAPTURED ***")
                print(f"URL: {url}")
                print(f"Body: {body[:500]}")

        async def on_response(resp):
            url = resp.url
            is_noise = any(n in url for n in NOISE)
            if ("connect/token" in url or "onboarding.derayah.com" in url or "api.derayah.com" in url) and not is_noise:
                try:
                    data = await resp.json()
                    print(f"\n*** AUTH RESPONSE ***")
                    print(json.dumps(data, indent=2)[:600])
                    if captured:
                        captured[-1]["response"] = data
                    OUT.write_text(json.dumps(captured, indent=2))
                    print(f"\nSaved to {OUT}")
                except Exception as e:
                    print(f"Response parse: {e}")

        tab.on("request", on_request)
        tab.on("response", on_response)

        # Wait up to 10 min for manual login
        for i in range(120):
            await asyncio.sleep(5)
            if captured:
                print("Done! Captured auth request.")
                break
            if "trading-portfolio" in tab.url or "dashboard" in tab.url:
                print("Logged in but no token request captured (may have used cookies)")
                break
            print(f"  Waiting... {(i+1)*5}s / 180s")

        OUT.write_text(json.dumps(captured, indent=2))
        print(f"Final capture: {len(captured)} requests saved to {OUT}")

asyncio.run(run())
