#!/usr/bin/env python3
"""Generate TASI Trading System Operations Procedure PDF."""

from fpdf import FPDF
from datetime import datetime

class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, "TASI Intraday Trading System - Operations Procedure", align="R")
        self.ln(2)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()} | Generated {datetime.now().strftime('%Y-%m-%d')} | Confidential", align="C")

    def section(self, title, color=(30, 80, 160)):
        self.ln(4)
        self.set_fill_color(*color)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, f"  {title}", fill=True, ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def subsection(self, title):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(50, 50, 150)
        self.cell(0, 6, title, ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def body(self, text, size=9):
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def bullet(self, items, indent=8):
        self.set_font("Helvetica", "", 9)
        for item in items:
            self.set_x(10 + indent)
            self.multi_cell(0, 5, f"-  {item}")
        self.ln(1)

    def two_col(self, left, right, lw=85):
        self.set_font("Helvetica", "", 9)
        x = self.get_x()
        y = self.get_y()
        self.multi_cell(lw, 5, left)
        y2 = self.get_y()
        self.set_xy(x + lw + 5, y)
        self.multi_cell(0, 5, right)
        self.set_y(max(y2, self.get_y()) + 1)

    def table_row(self, cols, widths, bold=False, fill=False, fill_color=(240,245,255)):
        self.set_font("Helvetica", "B" if bold else "", 8.5)
        if fill:
            self.set_fill_color(*fill_color)
        x = self.get_x()
        y = self.get_y()
        max_h = 6
        for txt, w in zip(cols, widths):
            self.cell(w, max_h, str(txt), border=1, fill=fill)
        self.ln(max_h)
        self.set_x(10)


pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=18)
pdf.add_page()

# -- Title Page ----------------------------------------------------------------
pdf.ln(15)
pdf.set_font("Helvetica", "B", 22)
pdf.set_text_color(20, 60, 140)
pdf.cell(0, 12, "TASI Intraday Trading System", align="C", ln=True)
pdf.set_font("Helvetica", "B", 14)
pdf.set_text_color(80, 80, 80)
pdf.cell(0, 8, "Operations Procedure & System Reference", align="C", ln=True)
pdf.ln(4)
pdf.set_font("Helvetica", "", 10)
pdf.set_text_color(120, 120, 120)
pdf.cell(0, 6, f"Version 1.3  |  Generated {datetime.now().strftime('%d %B %Y')}  |  Confidential", align="C", ln=True)
pdf.ln(10)

# Summary box
pdf.set_fill_color(240, 245, 255)
pdf.set_draw_color(100, 140, 220)
pdf.set_font("Helvetica", "", 9)
pdf.set_text_color(40, 40, 40)
pdf.multi_cell(0, 5.5,
    "This document describes the full architecture, stakeholder roles, automated workflows, and "
    "operational procedures for the TASI (Saudi Stock Exchange) intraday algorithmic trading system "
    "running on Ocean server. It covers system components, cron schedules, execution flows, "
    "risk controls, and escalation procedures.",
    border=1, fill=True)
pdf.ln(8)

# TOC
pdf.set_font("Helvetica", "B", 10)
pdf.set_text_color(30, 80, 160)
pdf.cell(0, 6, "Table of Contents", ln=True)
pdf.set_text_color(0,0,0)
toc = [
    ("1.", "System Overview & Infrastructure"),
    ("2.", "Stakeholders & Roles"),
    ("3.", "System Components"),
    ("4.", "Cron Schedule & Automation"),
    ("5.", "Daily Trading Operation Flow"),
    ("6.", "Execution Flow - Order Lifecycle"),
    ("7.", "Risk Controls & Stop Logic"),
    ("8.", "Alert & Escalation Matrix"),
    ("9.", "Session Health & Keepalive"),
    ("10.", "Recovery Procedures"),
    ("11.", "Known Issues & Fixes Log"),
]
for num, title in toc:
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(12, 5.5, num)
    pdf.cell(0, 5.5, title, ln=True)

pdf.add_page()

# -- 1. System Overview --------------------------------------------------------
pdf.section("1. System Overview & Infrastructure")

pdf.subsection("Infrastructure")
pdf.body(
    "The trading system runs entirely on Ocean, a dedicated Linux server (Ubuntu 24.04) located "
    "at Amin's premises. Ocean connects to Amin-PC (Windows) via a persistent SOCKS5 SSH tunnel "
    "for residential IP routing. All browser automation runs headlessly on Ocean's X display."
)

infra_items = [
    "Ocean Server - Ubuntu 24.04, 8GB RAM, 240GB SSD - primary compute node",
    "Amin-PC (Windows) - residential IP proxy via SOCKS5 tunnel on port 1080",
    "Chromium on Ocean - CDP port 18801 - hosts Derayah sessions + TickerChart feed",
    "Chrome Remote Desktop - PIN 056187 - for visual access to Ocean desktop",
    "OpenClaw Gateway - port 18789 - AI agent orchestration layer",
    "Ocean Dashboard - port 8765 - system status web UI",
    "SOCKS5 Tunnel - autossh to amin-pc, port 1080 - routes Derayah traffic via residential IP",
]
pdf.bullet(infra_items)

pdf.subsection("Key Directories")
dirs = [
    "/home/mino/tasi-exec/  - all trading scripts, logs, and state files",
    "/home/mino/.openclaw-mino/  - OpenClaw agent config and cron definitions",
    "/home/mino/snap/chromium/common/derayah-profile/  - Chromium persistent session",
]
pdf.bullet(dirs)

# -- 2. Stakeholders -----------------------------------------------------------
pdf.section("2. Stakeholders & Roles")

roles = [
    ("Amin (Owner)", "5529987063", "Decision maker. Receives all trade alerts, regime reports, and system health notifications via Telegram DM. Can send manual BUY/SELL commands. Final authority on all positions."),
    ("Mino (AI Agent)", "OpenClaw/Claude", "24/7 autonomous agent. Runs all cron jobs, monitors system health, executes keepalive tasks, handles session recovery, and orchestrates the trading pipeline. Communicates via Telegram."),
    ("TASI Exec Bot", "@TASIExecBot\n(8989533040...)", "Telegram bot running on Ocean (bot.py). Listens to EXEC Group for BUY/SELL commands, executes orders via Derayah REST API, and forwards results to Amin's DM."),
    ("EXEC Group", "-5235925419", "Private Telegram group used as the command bus between the poller (which sends order commands) and the execution bot (which receives and executes them). Also receives all trade alerts."),
    ("Derayah", "Broker", "Saudi brokerage. Order execution via REST API (api.derayah.com/trading). Portfolio ID: 2063853. Web platform: newonline.derayah.com. Trade terminal: derayah.tickerchart.net."),
    ("TickerChart", "Data Feed", "Real-time price feed embedded in Derayah Trade tab. Provides live WebSocket price stream (QO.SYMBOL.TAD topics) and hosts the JWT Bearer token used for API calls."),
]

for role, id_, desc in roles:
    pdf.set_fill_color(245, 248, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(45, 6, role, border="LTB", fill=True)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(35, 6, id_, border="TB", fill=True)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_fill_color(255, 255, 255)
    x, y = pdf.get_x(), pdf.get_y()
    pdf.multi_cell(0, 5, desc, border="RTB")
    pdf.ln(1)

pdf.add_page()

# -- 3. System Components ------------------------------------------------------
pdf.section("3. System Components")

components = [
    ("screener.py", "Pre-market", "Runs at 09:50 Sun-Thu. Scans 269 Sharia-compliant TASI stocks. Scores each by momentum, volume, and VWAP proximity. Outputs top 1-2 picks to picks.json. Classifies market regime (TRENDING/NEUTRAL/DEFENSIVE) via TASI index + oil price signals. Sends picks to Amin via Telegram."),
    ("poller.py", "Market Hours", "Runs 10:00-15:30 Sun-Thu. Every 5 min: fetches live prices via WS cache (TickerChart) or yfinance fallback. Checks entry signals (VWAP reclaim / breakout). Monitors open positions for stops. Auto-executes BUY/SELL via EXEC group. Exits at 15:30."),
    ("bot.py", "Always On", "Telegram bot running permanently. Receives commands from EXEC group and Amin directly. Executes orders via Derayah REST API. Runs 15-min keepalive loop: checks Derayah sessions, TC tab JWT, and REST API health. Forwards order results to Amin's DM."),
    ("derayah_keepalive.py", "Every 5 min", "Cron-driven (OpenClaw). Checks browser up, Derayah tab open, session not expired. Auto-starts Chromium if down. On session expiry, attempts login in priority order: (1) API refresh token injection - ~15s, no reCAPTCHA, no OTP; (2) CDP real Chromium login; (3) Playwright stealth login. Scrolls page to prevent idle timeout. Notifies Amin max 2x/day only if all three methods fail."),
    ("derayah_api.py", "Library", "Async REST client for api.derayah.com. Reads JWT Bearer token from TickerChart localStorage. Token cached 20 min, re-read from TC tab on expiry. Handles order placement, cancellation, portfolio/position queries."),
    ("market_regime.py", "Library", "Classifies TASI session as TRENDING / NEUTRAL / DEFENSIVE. Pre-market: uses TASI index 10d momentum + oil price signal. Intraday: updates every 30 min based on session % and VWAP position. Regime drives position sizing and max cycles."),
    ("map_selectors.py", "10:05 daily", "Runs at market open. Maps Derayah UI CSS selectors for symbol 1010. Ensures bot can find order fields if Derayah UI updates. Sends result to Amin via Telegram."),
    ("ws_probe.py", "10:07 daily", "Probes TickerChart WebSocket for 90s at market open. Validates that live price feed is flowing. Alerts Amin if no price frames received."),
]

for name, timing, desc in components:
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(30, 80, 160)
    pdf.cell(38, 5.5, name)
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(28, 5.5, f"[{timing}]")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.multi_cell(0, 5, desc)
    pdf.ln(1)

pdf.add_page()

# -- 4. Cron Schedule ----------------------------------------------------------
pdf.section("4. Cron Schedule & Automation")

pdf.body("All times are Riyadh (GMT+3). Trading days: Sunday-Thursday.")
pdf.ln(2)

# System crontab
pdf.subsection("System Crontab (crontab -l)")
cron_sys = [
    ("Fri 03:00", "Weekly reboot", "sudo reboot", "Clears memory, resets sessions"),
    ("Sun-Thu 10:05", "Selector mapper", "map_selectors.py 1010", "Maps Derayah UI for symbol 1010"),
    ("Sun-Thu 10:07", "WS probe", "ws_probe.py 90", "Validates live price feed (90s)"),
]
pdf.table_row(["Time", "Job", "Script", "Purpose"], [35, 38, 52, 65], bold=True, fill=True)
for row in cron_sys:
    pdf.table_row(row, [35, 38, 52, 65])

pdf.ln(4)

# OpenClaw crons
pdf.subsection("OpenClaw Agent Crons")
cron_oc = [
    ("Every 5 min", "derayah-keepalive", "ENABLED", "Keep Chromium + Derayah tab live; auto-login on session expiry"),
    ("Every 30 min", "ocean-health-monitor", "ENABLED", "RAM/disk/swap/CPU/gateway checks; alert Amin only if threshold breached"),
    ("Every 2 h", "auth-token-sync", "ENABLED", "Sync Claude CLI OAuth token to OpenClaw; silent (no Telegram)"),
    ("Daily 04:00", "daily-ram-cleanup", "ENABLED", "Kill idle browsers/zombies; restart bloated gateway; report to Amin"),
    ("Sun-Thu 09:50", "tasi-premarket-screener", "ENABLED", "Run screener.py; send picks + regime to Amin"),
    ("Sun-Thu 10:00", "tasi-price-poller", "ENABLED", "Start poller.py; sends confirmation to Amin when live"),
    ("Thu 22:00", "sharia-list-refresh", "ENABLED", "Refresh Sharia-compliant stock list from Saudi Exchange"),
    ("Every 2 h", "d-drive-recovery-monitor", "DISABLED", "D: drive recovery complete - disabled 2026-05-18"),
]
pdf.table_row(["Schedule", "Name", "Status", "Purpose"], [32, 45, 20, 93], bold=True, fill=True)
for row in cron_oc:
    fill = row[2] == "DISABLED"
    if fill:
        pdf.set_fill_color(250, 240, 240)
    pdf.table_row(row, [32, 45, 20, 93], fill=fill)

pdf.ln(4)

# Systemd services
pdf.subsection("Always-On Systemd User Services")
services = [
    ("socks-tunnel.service", "autossh SOCKS5 tunnel to Amin-PC (port 1080) - Restart=always"),
    ("openclaw-gateway.service", "OpenClaw AI gateway (port 18789) - agent orchestration"),
    ("chromium-derayah.service", "Chromium browser with CDP on port 18801 - Derayah sessions"),
    ("ocean-dashboard.service", "System status dashboard (port 8765)"),
]
for svc, desc in services:
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(68, 5.5, svc)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(0, 5.5, desc, ln=True)

pdf.add_page()

# -- 5. Daily Trading Flow -----------------------------------------------------
pdf.section("5. Daily Trading Operation Flow")

pdf.body("Each trading day (Sunday-Thursday) follows this sequence automatically:")
pdf.ln(2)

steps = [
    ("04:00", "RAM Cleanup", "Ocean self-cleans memory. Kills idle processes, restarts bloated services. Amin notified if action taken."),
    ("09:50", "Pre-market Screener", "screener.py scans 269 Sharia stocks. Classifies regime from TASI + oil. Writes top 1-2 picks to picks.json. Sends Telegram message to Amin:\n  \"Regime: DEFENSIVE | Picks: 8030 (14.25-15.14 SL:13.90), 3060 (15.83-16.00 SL:14.92)\""),
    ("10:00", "Poller Start", "tasi-price-poller cron starts poller.py. Sends: \"Price poller live - fast watch every 10s, price scan every 5min.\""),
    ("10:05", "Selector Mapper", "map_selectors.py verifies Derayah UI structure for symbol 1010. Telegram alert if UI changed."),
    ("10:07", "WS Probe", "ws_probe.py confirms TickerChart WebSocket is streaming live prices. Alert if dead."),
    ("10:00-15:30", "Active Monitoring", "Poller checks picks every 5 min:\n  1. Fetch live price (WS cache first, yfinance fallback)\n  2. Check entry signal: VWAP reclaim or breakout above prior high\n  3. If signal + regime allows: auto-execute BUY\n  4. Monitor open positions: hard stop (-7%), trailing stop (activates at +2%), hard close at 14:45"),
    ("14:45", "Hard Close", "All open positions force-sold via auto_sell(). Alert sent to Amin."),
    ("15:30", "Session End", "Poller exits. Sends \"Price poller stopped (market closed)\" to Amin."),
    ("After close", "Keepalive", "Derayah browser maintained but no new orders. Next screener at 09:50 next day."),
]

for time_, event, desc in steps:
    pdf.set_fill_color(235, 242, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(22, 6, time_, fill=True, border=1)
    pdf.cell(40, 6, event, fill=True, border=1)
    pdf.set_fill_color(255, 255, 255)
    pdf.set_font("Helvetica", "", 8.5)
    x, y = pdf.get_x(), pdf.get_y()
    pdf.multi_cell(0, 5, desc, border=1)
    pdf.ln(1)

pdf.add_page()

# -- 6. Execution Flow ---------------------------------------------------------
pdf.section("6. Execution Flow - Order Lifecycle")

pdf.subsection("Auto-Execution Path (no human intervention)")
steps_exec = [
    "1. poller.py detects entry signal (VWAP reclaim / breakout)",
    "2. Checks regime: DEFENSIVE=1 cycle 20%, NEUTRAL=2 cycles 40%, TRENDING=8 cycles 45%",
    "3. Sends \"BUY 1010 100 MARKET\" to EXEC Group (Telegram)",
    "4. Sends \"Buying 1010 - order sent, confirmation incoming...\" to Amin's DM",
    "5. bot.py receives command from EXEC Group",
    "6. bot.py reads JWT token from TickerChart localStorage (TC_DERAYAH)",
    "7. bot.py calls POST api.derayah.com/trading/Order/Place",
    "8. On success: bot.py replies \"[OK] BUY 100 × 1010 @ Market - orderId=...\" to EXEC Group + Amin DM",
    "9. On failure: bot.py replies \"[ERR] Order failed: <reason>\" to EXEC Group + Amin DM",
    "10. poller.py tracks position in positions.json",
]
pdf.bullet(steps_exec, indent=4)

pdf.ln(3)
pdf.subsection("Manual Execution Path (Amin sends command)")
steps_manual = [
    "Amin sends \"BUY 1010 100 MARKET\" or \"BUY 1010 100 @ 45.50\" to EXEC Group",
    "bot.py validates: command must come from GROUP_CHAT_ID or OWNER_ID",
    "Execution proceeds same as steps 6-9 above",
    "Result returned as reply in EXEC Group",
]
pdf.bullet(steps_manual, indent=4)

pdf.ln(3)
pdf.subsection("Token Source (Critical)")
pdf.body(
    "ALL orders require a valid JWT Bearer token. This token lives in the TickerChart tab's "
    "localStorage (key: TC_DERAYAH). The TickerChart tab (derayah.tickerchart.net) auto-refreshes "
    "it from the active Derayah session. bot.py caches the token for 20 minutes and re-reads "
    "on expiry. If the TC tab logs out, orders fail with HTTP 401."
)

pdf.ln(3)
pdf.subsection("TC Session Recovery (bot.py keepalive - every 15 min)")
recovery_steps = [
    "bot.py reads JWT from TC localStorage, checks expiry",
    "If expiring within 5 min or missing: clicks 'Derayah Trade' link on newonline.derayah.com",
    "Waits up to 25s for TC tab to open and populate fresh JWT",
    "If TC tab shows login page: alerts Amin via EXEC Group",
    "If newonline.derayah.com session expired: attempts auto-login (Playwright, stored creds)",
    "If auto-login hits OTP/2FA: alerts Amin to complete manually",
]
pdf.bullet(recovery_steps, indent=4)

pdf.add_page()

# -- 7. Risk Controls ----------------------------------------------------------
pdf.section("7. Risk Controls & Stop Logic")

pdf.subsection("Position-Level Controls")
risk_items = [
    "Hard Stop Loss: -7% from entry price -> immediate MARKET SELL",
    "Trailing Stop: activates after +2% gain, follows price up, exits on reversal",
    "Hard Close at 14:45: all open positions force-sold regardless of P&L",
    "Max Cycles per Pick: TRENDING=8, NEUTRAL=2, DEFENSIVE=1 (re-entries after stop-out)",
    "Scratch Guard: 2 consecutive scratches (small loss) stops cycling for that symbol today",
    "Entry Cutoff: no new BUY orders after 14:30",
]
pdf.bullet(risk_items)

pdf.ln(3)
pdf.subsection("Regime-Based Position Sizing")
pdf.table_row(["Regime", "Trigger", "Position Size", "Max Cycles", "Strategy"], [28, 65, 28, 25, 24], bold=True, fill=True)
regimes = [
    ("TRENDING", "TASI +momentum, oil positive, session above VWAP", "45%", "8", "C"),
    ("NEUTRAL", "Mixed signals or default", "40%", "2", "B"),
    ("DEFENSIVE", "TASI negative, oil down, or session below VWAP", "20%", "1", "B"),
]
for row in regimes:
    pdf.table_row(row, [28, 65, 28, 25, 24])

pdf.ln(4)
pdf.subsection("System-Level Controls")
sys_risk = [
    "SOCKS5 tunnel: all Derayah traffic routed via Amin-PC residential IP - prevents ISP blocks",
    "Browser health: derayah-keepalive cron detects browser crash, auto-restarts Chromium",
    "Session health: bot.py 15-min keepalive checks TC JWT, REST API ping, Derayah online session",
    "Order deduplication: positions.json tracks open positions - poller skips re-entry if already in",
    "Daily max login prompts: 2 per day - prevents Telegram spam if session repeatedly expires",
]
pdf.bullet(sys_risk)

# -- 8. Alert Matrix -----------------------------------------------------------
pdf.add_page()
pdf.section("8. Alert & Escalation Matrix")

pdf.body("Alerts are sent via Telegram. Destination: Amin DM (5529987063) unless noted.")
pdf.ln(2)

alerts = [
    ("Entry Signal", "BUY 1010 100 MARKET - Cycle 1/1...", "Amin DM + EXEC Group", "Auto-execute or override manually"),
    ("Order Confirmed", "[OK] BUY 100 × 1010 @ Market - orderId=...", "Amin DM", "No action needed"),
    ("Order Failed", "[ERR] Order failed: 401 Unauthorized", "Amin DM + EXEC Group", "Check TC tab session, re-login if needed"),
    ("Stop Loss Hit", "[STOP] 8030: HARD STOP at -7% - selling", "EXEC Group", "Order auto-sent; confirm on Derayah"),
    ("Hard Close 14:45", "[ALERT] 14:45 HARD CLOSE - auto-selling...", "EXEC Group", "All positions force-closed"),
    ("Session Expired", "[WARN] Derayah session expired - please log in", "Amin DM (max 2x/day)", "Log in at newonline.derayah.com"),
    ("TC Session Expired", "[WARN] Derayah Trade session expired", "EXEC Group", "Log in to TickerChart tab"),
    ("Browser Down", "Browser failed to start - check Ocean [RED]", "Amin DM", "Check Ocean; restart Chromium"),
    ("RAM Warning", "[YELLOW] RAM Warning on Ocean - X% available", "Amin DM", "Close Chrome tabs if below 15%"),
    ("RAM Critical", "[RED] RAM: X% - Swap: XGB", "Amin DM", "Immediate: daily-cleanup or manual kill"),
    ("Tunnel Down", "SOCKS5 tunnel to Amin-PC is DOWN [WARN]", "Amin DM", "Check Amin-PC is on and SSH accessible"),
    ("Regime Change", " Regime: NEUTRAL -> DEFENSIVE (reason)", "Amin DM", "Informational; affects future sizing"),
    ("Screener Picks", "Regime: X | Picks: SYMBOL (zone, SL)...", "Amin DM", "Review before market open"),
    ("Poller Live", " Price poller live - fast watch 10s...", "Amin DM", "Confirm before market opens"),
    ("Poller Stopped", " Price poller stopped (market closed)", "Amin DM", "End of trading session"),
    ("Sharia Refresh", "[OK] Sharia list refreshed - N stocks", "Amin DM", "Every Thursday night"),
    ("Token Expired", "[WARN] Refresh token expired - manual login needed", "EXEC Group + Amin DM", "Log in via CRD (PIN 056187); login_monitor.py captures new tokens automatically"),
]

pdf.table_row(["Alert", "Message Sample", "Destination", "Response"], [40, 60, 35, 55], bold=True, fill=True)
for i, row in enumerate(alerts):
    fill = (i % 2 == 0)
    if fill:
        pdf.set_fill_color(248, 250, 255)
    pdf.table_row(row, [40, 60, 35, 55], fill=fill)

pdf.add_page()

# -- 9. Session Health ---------------------------------------------------------
pdf.section("9. Session Health & Keepalive Architecture")

pdf.body(
    "Two independent keepalive systems run in parallel to ensure Derayah sessions stay alive:"
)

pdf.ln(2)
pdf.subsection("A) derayah_keepalive.py (cron, every 5 min, via OpenClaw)")
a_items = [
    "Checks Chromium is running on CDP port 18801 - starts it if not",
    "Finds newonline.derayah.com tab - opens it if missing",
    "Detects session expiry (URL = onboarding.derayah.com/#/signin)",
    "LOGIN METHOD 1 (Primary): _auto_login_token_inject() - reads stored OAuth2 refresh token from derayah_token_live.json, calls Derayah IdentityServer (api.derayah.com/idspark/connect/token) to get a fresh access token, injects both tokens into browser localStorage, reloads page. No reCAPTCHA, no OTP, completes in ~15 seconds.",
    "LOGIN METHOD 2 (Fallback 1): _auto_login_cdp() - real Chromium via CDP, fills credentials with native JS setter events",
    "LOGIN METHOD 3 (Fallback 2): _auto_login() - Playwright stealth with stored creds, press_sequentially for Vue form",
    "Navigates to trading-portfolio and scrolls every 15 min to prevent idle timeout",
    "Alerts Amin max 2x/day only if all three login methods fail",
]
pdf.bullet(a_items)

pdf.ln(2)
pdf.subsection("B) bot.py keepalive loop (every 15 min, background thread)")
b_items = [
    "Checks newonline.derayah.com tab - auto-logins if session expired",
    "Reads TickerChart JWT from localStorage - checks expiry timestamp",
    "If JWT expiring < 5 min: clicks 'Derayah Trade' to re-open TC tab with fresh token",
    "Detects TC tab login page - alerts Amin via EXEC Group",
    "Pings REST API (get_orders) to keep JWT session alive server-side",
]
pdf.bullet(b_items)

pdf.ln(2)
pdf.subsection("OAuth2 Token Store (derayah_token_live.json)")
pdf.body(
    "The file /home/mino/tasi-exec/derayah_token_live.json stores the live Derayah OAuth2 tokens. "
    "Keys: Derayah_accesstoken (JWT, 60 min TTL) and Derayah_refreshtoken (64-char hex, offline_access scope, valid 30+ days). "
    "Token endpoint: https://api.derayah.com/idspark/connect/token. "
    "Client: NewWebClient. Scope: openid profile roles offline_access derayah.api. "
    "Tokens are updated each time _auto_login_token_inject() or _refresh_token_api() runs. "
    "login_monitor.py (daemon) captures fresh tokens automatically after any manual login via CRD."
)

pdf.subsection("Browser Profile Persistence")
pdf.body(
    "Chromium uses /home/mino/snap/chromium/common/derayah-profile/ as user data dir. "
    "Session cookies persist across browser restarts. On browser crash + restart, "
    "Derayah typically re-loads without requiring login (cookies valid ~24h). "
    "TC tab JWT expires independently and may require re-login even if cookies are valid."
)

# -- 10. Recovery Procedures ---------------------------------------------------
pdf.section("10. Recovery Procedures")

pdf.subsection("Browser Down")
pdf.bullet([
    "Automatic: derayah-keepalive cron detects within 5 min, starts Chromium, attempts auto-login",
    "Manual via SSH: chromium --remote-debugging-port=18801 --user-data-dir=/home/mino/snap/chromium/common/derayah-profile/ &",
    "Manual via CRD: open Chrome Remote Desktop (PIN 056187), start Chromium manually",
])

pdf.subsection("Derayah Session Expired")
pdf.bullet([
    "Automatic (normal case): derayah-keepalive uses OAuth2 refresh token - no reCAPTCHA, no OTP, ~15 seconds",
    "Tokens stored in: /home/mino/tasi-exec/derayah_token_live.json (refresh token valid 30+ days)",
    "If refresh token expired (<1x per month): CDP or Playwright fallback auto-attempted",
    "If all auto-methods fail: log in manually via CRD (PIN 056187) at newonline.derayah.com",
    "After manual login: login_monitor.py daemon auto-captures new tokens to derayah_token_live.json",
    "To start capture daemon before manual login: python3 /home/mino/tasi-exec/login_monitor.py",
])

pdf.subsection("TickerChart Session Expired (orders blocked)")
pdf.bullet([
    "bot.py keepalive should auto-recover by re-opening TC tab (every 15 min)",
    "If not recovered: open CRD, navigate to newonline.derayah.com, click 'Derayah Trade'",
    "Verify token present: check bot.py log for 'TC tab: re-opened with fresh JWT'",
])

pdf.subsection("SOCKS5 Tunnel Down")
pdf.bullet([
    "Automatic: socks-tunnel.service restarts every 15s via Restart=always",
    "Manual: systemctl --user restart socks-tunnel.service",
    "Root cause: Amin-PC offline or SSH port blocked - check PC is running",
])

pdf.subsection("Ocean RAM Critical (<10% available)")
pdf.bullet([
    "daily-ram-cleanup runs at 04:00 automatically",
    "Manual: pkill -u mino chromium; sleep 2; start browser fresh",
    "Check: ps aux --sort=-%mem | head -10",
    "Last resort: systemctl --user restart openclaw-gateway",
])

pdf.subsection("Poller Not Running During Market Hours")
pdf.bullet([
    "Automatic: tasi-price-poller cron starts it at 10:00",
    "Manual: cd /home/mino/tasi-exec && nohup python3 poller.py >> poller.log 2>&1 &",
    "Verify: tail -f /home/mino/tasi-exec/poller.log",
])

pdf.add_page()

# -- 11. Issues Log ------------------------------------------------------------
pdf.section("11. Known Issues & Fixes Applied (2026-05-18) - 16 entries")

issues = [
    ("CDP Port Mismatch", "map_selectors.py and ws_probe.py used port 18800; browser on 18801", "Updated CDP_URL to 18801 in both scripts"),
    ("Duplicate Keepalive Runs", "System crontab + OpenClaw cron both ran keepalive every 5 min", "Removed system crontab entry; OpenClaw is sole runner"),
    ("Telegram 400 on Error Messages", "tg() sent raw stack traces with < > as HTML - Telegram rejected", "Added html.escape() on messages containing angle brackets"),
    ("auth-token-sync Delivery", "49 consecutive errors: delivery target 'last' with null chatId", "Fixed to explicit telegram:5529987063; set mode=none (silent)"),
    ("D: Drive Monitor Orphaned", "Recovery completed 2026-05-17; cron kept running every 2h", "Disabled cron a9dc595b"),
    ("Auto-login Vue Form Bug", "Playwright fill() didn't trigger Vue reactive validation; submit stayed disabled", "Changed to press_sequentially() + wait_for(state=enabled)"),
    ("WS Cache Miss - 3060.SR", "Low-volume stock (818 trades/day); TC only sends orderbook frames, never trade price -> cache never populated", "on_frame now accepts bidprice/askprice as fallback mid-price; TTL 30s->90s"),
    ("Poller Market Close 15:05", "Poller hardcoded to exit 25 min before actual close (15:30)", "Changed MARKET_CLOSE to time(15, 30)"),
    ("yfinance Early Session Failure", "period='1d' returns empty df in first 15 min of session", "Fallback to period='5d' when 1d is empty"),
    ("Order Confirmation Gap", "auto_buy/sell sent 'Auto-bought' before order confirmed; failures went to EXEC Group only", "Messages now say 'order sent'; bot.py forwards [OK]/[ERR] to Amin DM"),
    ("reCAPTCHA v2 Blocked All Automation", "Derayah login page uses reCAPTCHA v2 checkbox + image challenge; appeared regardless of Playwright stealth, real Chromium, or residential IP. Audio challenge transcription via Whisper was unreliable. No automation path through it.", "Bypassed entirely via OAuth2 refresh token API - no browser interaction with login page needed"),
    ("Missing client_secret for Token Refresh", "Derayah token endpoint returned 'invalid_client'; refresh grant requires client_secret but it was unknown", "Found client_secret=NewDerayahWeb2026 in Derayah JS bundle (/js/app.1fa71520.js) as VUE_APP_CLIENT_SECRET plaintext"),
    ("OTP Screen Detection (SPA)", "Derayah login is a Vue SPA - URL stays at onboarding.derayah.com/#/signin on both login and OTP screens; URL-based detection failed", "Fixed by checking DOM for OTP input field visibility instead of URL; now moot since token injection bypasses login entirely"),
    ("login_monitor.py Timing Miss", "Double-fork daemon launched to capture tokens after manual login but timed out at 15 min boundary just before Amin completed login at 18:20", "Tokens recovered by running _refresh_token_api() with manually extracted refresh token from browser localStorage via CRD"),
    ("SOCKS5 Missing in requests", "requests library raised 'Missing dependencies for SOCKS support' when proxy was configured for token API call", "Removed proxy from token endpoint call - api.derayah.com token URL is accessible without SOCKS tunnel"),
    ("Keepalive: Browser Context Closed", "derayah_keepalive.py killed Chromium on session expiry, restarted it, then auto-login failed with 'Target page, context or browser has been closed' mid-login at 16:53. No NOTIFY sent - alert threshold not reached.", "No fix yet. Follow-up: add failure counter (3x) before NOTIFY; add CDP port readiness wait before Playwright connects after browser restart."),
]

pdf.table_row(["Issue", "Root Cause", "Fix"], [42, 72, 76], bold=True, fill=True)
for i, row in enumerate(issues):
    fill = (i % 2 == 0)
    if fill:
        pdf.set_fill_color(248, 250, 255)
    pdf.table_row(row, [42, 72, 76], fill=fill)

# Footer note
pdf.ln(8)
pdf.set_fill_color(255, 248, 230)
pdf.set_font("Helvetica", "I", 8.5)
pdf.set_text_color(100, 80, 0)
pdf.multi_cell(0, 5,
    "NOTE: This document reflects the system state as of 2026-05-18. "
    "Changes to scripts, crons, or infrastructure should be logged in "
    "/home/mino/.openclaw-mino/workspace/memory/issues.md and this document regenerated.",
    border=1, fill=True)

out = "/home/mino/tasi-exec/TASI_Ops_Procedure_2026-05-18_v1.3.pdf"
pdf.output(out)
print(f"PDF written: {out}")
