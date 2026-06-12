#!/usr/bin/env python3
"""
TASI Failure Detection & Auto-Coding System v2
Enhanced monitoring with detailed analysis
Usage: python3 auto_coder_consult.py [report|monitor|clear]
"""

import subprocess
import json
import os
import re
import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path("/home/mino/tasi-exec")
FAILURE_LOG = BASE_DIR / "failure_logs.json"
DAILY_REPORT = BASE_DIR / "reports/daily_failures"
CODER_MODEL = "qwen3-coder:480b-cloud"

DAILY_REPORT.parent.mkdir(parents=True, exist_ok=True)

FAILURE_PATTERNS = {
    "websocket_failure": {
        "patterns": [
            r"WS cache miss",
            r"WebSocket.*dead",
            r"webSocketFrameReceived.*error",
            r"WS listener.*no TC tab",
            r"WebSocket blank",
        ],
        "severity": "medium",
        "description": "WebSocket price data not flowing",
    },
    "token_failure": {
        "patterns": [
            r"TOKEN EXPIRED",
            r"token refresh failed",
            r"invalid_grant",
            r"TC_DERAYAH token.*not found",
            r"401\b",
        ],
        "severity": "high",
        "description": "Authentication token issues",
    },
    "browser_failure": {
        "patterns": [
            r"Browser failed to start",
            r"no Derayah tab",
            r"CDP.*error",
            r"Chrome.*crash",
            r"SingletonLock",
            r"No such target id",
        ],
        "severity": "high",
        "description": "Browser/CDP connectivity issues",
    },
    "execution_failure": {
        "patterns": [
            r"Cannot click element",
            r"Execution failed",
            r"Order failed",
            r"Trade.*error",
            r"BUY.*failed",
            r"SELL.*failed",
        ],
        "severity": "critical",
        "description": "Trade execution failures",
    },
    "capital_failure": {
        "patterns": [
            r"CAPITAL.*error",
            r"capital_tracker.*failed",
            r"NameError.*CAPITAL",
        ],
        "severity": "high",
        "description": "Capital management issues",
    },
    "screener_failure": {
        "patterns": [
            r"screener.*failed",
            r"No picks generated",
            r"yfinance.*error",
            r"screening.*timeout",
        ],
        "severity": "medium",
        "description": "Stock screening issues",
    },
    "keepalive_failure": {
        "patterns": [
            r"keepalive.*failed",
            r"Session expired",
            r"Auto-login failed",
        ],
        "severity": "high",
        "description": "Session maintenance failures",
    },
}


class FailureDetector:
    def __init__(self):
        self.seen_hashes = set()
        self.load_seen()
    
    def load_seen(self):
        if FAILURE_LOG.exists():
            try:
                with open(FAILURE_LOG) as f:
                    data = json.load(f)
                for failure in data.get("failures", []):
                    self.seen_hashes.add(failure.get("hash", ""))
            except:
                pass
    
    def get_hash(self, line: str) -> str:
        """Generate hash for deduplication - ignore timestamps."""
        # Remove timestamps to deduplicate same error
        cleaned = re.sub(r'\[?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]?', '', line)
        cleaned = re.sub(r'\d+s ago', '', cleaned)
        cleaned = re.sub(r'\d+ min', '', cleaned)
        return hashlib.md5(cleaned.strip().encode()).hexdigest()[:12]
    
    def is_recent_line(self, line: str, cutoff: datetime) -> bool:
        patterns = [
            r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]",
            r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})[,.]",
        ]
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                try:
                    ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    return ts > cutoff
                except:
                    pass
        return True
    
    def analyze_failure(self, log_line: str, log_file: str) -> dict:
        failure = {
            "timestamp": datetime.now().isoformat(),
            "log_file": log_file,
            "raw_line": log_line,
            "hash": self.get_hash(log_line),
            "category": "unknown",
            "severity": "low",
            "description": "",
            "context": "",
        }
        
        for category, info in FAILURE_PATTERNS.items():
            for pattern in info["patterns"]:
                if re.search(pattern, log_line, re.IGNORECASE):
                    failure["category"] = category
                    failure["severity"] = info["severity"]
                    failure["description"] = info["description"]
                    break
        
        # Extract context
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if log_line.strip() in line.strip():
                    start = max(0, i - 5)
                    end = min(len(lines), i + 6)
                    failure["context"] = "".join(lines[start:end])
                    break
        except:
            pass
        
        # Extract timestamp from log line
        ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", log_line)
        if ts_match:
            failure["log_timestamp"] = ts_match.group(1)
        
        return failure
    
    def consult_coder(self, failure: dict) -> dict:
        prompt = f"""TASI Trading System Failure Analysis

Category: {failure['category']}
Severity: {failure['severity']}
Description: {failure['description']}
Time: {failure['timestamp']}
Log File: {failure['log_file']}

Failure Line:
{failure['raw_line']}

Context:
{failure['context']}

Analyze and provide:
1. Root cause
2. Immediate fix (code if applicable)
3. Prevention strategy
"""
        
        try:
            result = subprocess.run(
                ["ollama", "run", CODER_MODEL, prompt],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                return {"raw_analysis": result.stdout, "status": "completed"}
            return {"error": result.stderr, "status": "failed"}
        except Exception as e:
            return {"error": str(e), "status": "error"}
    
    def log_failure(self, failure: dict, analysis: dict):
        entry = {**failure, "analysis": analysis, "status": "analyzed"}
        
        if FAILURE_LOG.exists():
            try:
                with open(FAILURE_LOG) as f:
                    data = json.load(f)
            except:
                data = {"failures": [], "summary": {}}
        else:
            data = {"failures": [], "summary": {}}
        
        data["failures"].append(entry)
        
        category = failure["category"]
        if category not in data["summary"]:
            data["summary"][category] = {"count": 0, "last_seen": ""}
        data["summary"][category]["count"] += 1
        data["summary"][category]["last_seen"] = failure["timestamp"]
        
        data["stats"] = {
            "total_failures": len(data["failures"]),
            "last_check": datetime.now().isoformat(),
        }
        
        with open(FAILURE_LOG, "w") as f:
            json.dump(data, f, indent=2)
        
        return entry
    
    def monitor(self, max_age_minutes=30):
        """Monitor logs for recent failures only."""
        
        log_files = [
            BASE_DIR / "keepalive.log",
            BASE_DIR / "poller.log",
            BASE_DIR / "exec.log",
            BASE_DIR / "screener.log",
        ]
        
        new_failures = []
        cutoff_time = datetime.now() - timedelta(minutes=max_age_minutes)
        
        # Skip during closed market if only websocket issues
        from datetime import datetime as dt
        import pytz
        riyadh = pytz.timezone('Asia/Riyadh')
        now = dt.now(riyadh)
        is_trading = (now.weekday() not in {4, 5}) and (10 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30))
        
        for log_file in log_files:
            if not log_file.exists():
                continue
            
            try:
                # Check file modification time
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < cutoff_time:
                    continue
                
                result = subprocess.run(
                    ["tail", "-20", str(log_file)],
                    capture_output=True, text=True
                )
                lines = result.stdout.strip().split("\n")
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    if not self.is_recent_line(line, cutoff_time):
                        continue
                    
                    matched = False
                    for category, info in FAILURE_PATTERNS.items():
                        # During closed market, skip non-critical websocket issues
                        if not is_trading and category == "websocket_failure":
                            continue
                        
                        for pattern in info["patterns"]:
                            if re.search(pattern, line, re.IGNORECASE):
                                h = self.get_hash(line)
                                if h in self.seen_hashes:
                                    continue
                                self.seen_hashes.add(h)
                                
                                failure = self.analyze_failure(line, str(log_file))
                                analysis = self.consult_coder(failure)
                                logged = self.log_failure(failure, analysis)
                                new_failures.append(logged)
                                matched = True
                                break
                        if matched:
                            break
                        
            except Exception as e:
                print(f"Error monitoring {log_file}: {e}")
        
        return new_failures
    
    def generate_report(self) -> str:
        if not FAILURE_LOG.exists():
            return "No failures logged yet. ✅"
        
        with open(FAILURE_LOG) as f:
            data = json.load(f)
        
        failures = data.get("failures", [])
        if not failures:
            return "No failures logged yet. ✅"
        
        cutoff = datetime.now() - timedelta(hours=24)
        recent = [f for f in failures if datetime.fromisoformat(f["timestamp"]) > cutoff]
        
        if not recent:
            return "No failures in last 24 hours. ✅"
        
        report = f"""📊 TASI Failure Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}
═══════════════════════════════════════════

📈 Summary (Last 24h):
   Total failures: {len(recent)}
   Unique types: {len(set(f['category'] for f in recent))}
   Critical: {sum(1 for f in recent if f.get('severity') == 'critical')}
   High: {sum(1 for f in recent if f.get('severity') == 'high')}
   Medium: {sum(1 for f in recent if f.get('severity') == 'medium')}

"""
        
        by_category = {}
        for f in recent:
            cat = f["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)
        
        for category, items in by_category.items():
            report += f"\n🔴 {category.upper()} ({len(items)} occurrences)\n"
            report += f"   Severity: {items[0].get('severity', 'unknown')}\n"
            report += f"   Description: {items[0].get('description', 'N/A')}\n\n"
            
            for f in items[-3:]:
                report += f"   • {f.get('log_timestamp', f['timestamp'])}\n"
                report += f"     File: {f['log_file']}\n"
                report += f"     Line: {f['raw_line'][:80]}...\n\n"
        
        return report


def main():
    detector = FailureDetector()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "report":
            print(detector.generate_report())
        elif sys.argv[1] == "monitor":
            failures = detector.monitor(max_age_minutes=30)
            if failures:
                print(f"🚨 Detected {len(failures)} NEW failures:")
                for f in failures:
                    print(f"   • [{f.get('severity','unknown').upper()}] {f.get('category','unknown')}: {f['raw_line'][:60]}")
                sys.exit(1)  # Error code = notify
            else:
                print("✅ No new failures detected.")
                sys.exit(0)  # Success = silent
        elif sys.argv[1] == "clear":
            if FAILURE_LOG.exists():
                os.remove(FAILURE_LOG)
                print("Cleared failure log.")
        else:
            print("Usage: auto_coder_consult.py [report|monitor|clear]")
    else:
        failures = detector.monitor()
        if failures:
            print(f"🚨 Detected {len(failures)} new failures.")
            sys.exit(1)
        else:
            print("✅ No new failures detected.")
            sys.exit(0)


if __name__ == "__main__":
    main()
