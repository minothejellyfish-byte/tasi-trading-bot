# Hard close logic - fixed version
# Phase 1: 14:30-14:49 - VWAP exits
if now_time >= HARD_CLOSE_START and now_time < HARD_CLOSE_END and "hard_close_p1" not in _alerted:
    positions = load_positions_cached()
    open_syms = [s for s, p in positions.items() if not p.get("closed")]
    
    if open_syms:
        tg_send(f"⏰ Hard Close Window (14:30-14:50) - {len(open_syms)} position(s) still open")
        for s in open_syms:
            price, df_pos, _, ws_vwap = fetch_data(f"{s}.SR")
            vwap_now = ws_vwap if ws_vwap is not None else (calc_vwap(df_pos) if df_pos is not None else None)
            entry = positions[s].get("entry_price", 0)
            gain_pct = (price - entry) / entry if entry else 0
            
            exit_reason = ""
            if vwap_now and price >= vwap_now and gain_pct >= -0.01:
                exit_reason = f"📊 Above VWAP ({price:.2f} >= {vwap_now:.2f}) - waiting for better exit until 14:50"
                tg_send(f"{s}: {exit_reason}")
                continue
            elif vwap_now and price < vwap_now and gain_pct < 0:
                exit_reason = f"📉 Below VWAP ({price:.2f} < {vwap_now:.2f}) - cutting loss at {gain_pct*100:.1f}%"
            elif gain_pct < -0.03:
                exit_reason = f"🛑 Deep loss {gain_pct*100:.1f}% - exiting now"
            else:
                exit_reason = f"⏳ Small {gain_pct*100:.1f}% - monitoring until 14:50"
                tg_send(f"{s}: {exit_reason}")
                continue
            
            auto_sell(s, positions[s].get("qty", "?"), f"Hard Close | {exit_reason}",
                      trigger_basis=TRIGGER_HARD_CLOSE,
                      trigger_detail=f"Hard close: {exit_reason}")
    
    _alerted.add("hard_close_p1")
    log.info("Hard close Phase 1 (VWAP exits) processed")

# Phase 2: At or after 14:50 - FORCE SELL ALL remaining
if now_time >= HARD_CLOSE_END and "hard_close_p2" not in _alerted:
    positions = load_positions_cached()
    remaining = [s for s, p in positions.items() if not p.get("closed")]
    
    if remaining:
        tg_send(f"⏰ HARD CLOSE 14:50 - Force selling {len(remaining)} remaining position(s)")
        for s in remaining:
            qty = positions[s].get("qty", "?")
            price, _, _, _ = fetch_data(f"{s}.SR")
            entry = positions[s].get("entry_price", 0)
            gain_pct = (price - entry) / entry if entry else 0
            auto_sell(s, qty, f"⏰ HARD CLOSE 14:50 - Force market sell | {gain_pct*100:+.1f}%",
                      trigger_basis=TRIGGER_HARD_CLOSE,
                      trigger_detail="Hard close 14:50 forced exit")
            log.info(f"HARD CLOSE forced sell: {s} qty={qty} at {price:.2f} ({gain_pct*100:.1f}%)")
    
    _alerted.add("hard_close_p2")
    log.info("Hard close Phase 2 (force sell) processed")
    
    # Create stand-down marker file
    try:
        stand_down_path = f"{BASE_DIR}/stand_down"
        if not os.path.exists(stand_down_path):
            with open(stand_down_path, "w") as f:
                f.write(f"STAND DOWN activated at {datetime.now(RIYADH).isoformat()}\n")
                f.write("No new buys allowed until next session\n")
                f.write("Remove this file before next trading day\n")
            log.info("STAND DOWN mode activated - no new buys until tomorrow")
    except Exception as e:
        log.error(f"Failed to create stand_down marker: {e}")
