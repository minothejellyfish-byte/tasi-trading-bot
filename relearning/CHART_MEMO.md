# Memo: Chart Creation Reference

## Date: June 16, 2026

## Data Sources

### 1. Price Data (Primary)
**File:** `ws_prices_YYYY-MM-DD.jsonl`  
**Location:** `/home/mino/tasi-exec/`  
**Format:** JSON Lines, one tick per line

**Available fields:**
| Field | Type | Description |
|-------|------|-------------|
| `ts` | float | Unix timestamp |
| `time` | string | ISO datetime (e.g., "2026-06-16T10:00:20") |
| `symbol` | string | Stock code (e.g., "1213") |
| `price` | float | Current price |
| `change` | float | Absolute change from previous tick |
| `pchange` | float | Percent change from previous tick |
| `real` | boolean | `true` = actual market data, `false` = interpolated/filled |

**Key usage:**
- `price` → Blue line (main price trace)
- `real` → Activity band width proxy (more real ticks = higher activity)
- No volume, no bid/ask, no order book data

### 2. Trade Data
**File:** `history/order_history.csv`  
**Location:** `/home/mino/tasi-exec/history/`  
**Format:** CSV with columns:

| Column | Index | Example | Description |
|--------|-------|---------|-------------|
| Date | 0 | `06-16` | MM-DD format |
| Time | 1 | `10:03` | HH:MM |
| Order ID | 2 | `85` | Order identifier |
| Symbol | 3 | `1213` | Stock code |
| Side | 4 | `BUY` / `SELL` | Trade direction |
| Qty | 5 | `14` | Quantity |
| Price | 6 | `23.9` | Execution price |
| Total | 7 | `334.6` | Total value |
| Fee | 8 | `0.19` | Commission |
| Type | 9 | `LIMIT` / `MARKET` | Order type |
| Status | 10 | `FILLED` / `REJECTED` | Order status |
| Method | 11 | `auto_buy` | Execution method |
| Trigger | 12 | `pick_entry` | Entry/exit trigger basis |

**Critical:** Status column is at index **10**, not 5.

### 3. Entry Zone Data
**File:** `picks_analysis.json`  
**Location:** `/home/mino/tasi-exec/relearning/daily/YYYY-MM-DD/`  

**Fields per pick:**
- `symbol` — Stock code
- `entry_low` — Lower entry zone boundary
- `entry_high` — Upper entry zone boundary
- `score` — Screener score
- `tier` — Pick tier (main/backup)

### 4. Additional Data Sources

| File | Location | Contains |
|------|----------|----------|
| `market_state.json` | `relearning/daily/YYYY-MM-DD/` | Market regime, active picks |
| `trades_analysis.json` | `relearning/daily/YYYY-MM-DD/` | Trade performance stats |
| `entry_exit_stats.json` | `relearning/daily/YYYY-MM-DD/` | Win rate, slippage |
| `missed_opportunities.json` | `relearning/daily/YYYY-MM-DD/` | Stocks that moved outside picks |

---

## Chart Specifications

### 1. Price Line
- **Color:** Blue
- **Source:** `price` from websocket log
- **Style:** Solid line, linewidth 2.5

### 2. Trade Markers
- **Shape:** Circles (not triangles)
- **BUY:** Green circle (`lime` fill, `darkgreen` edge)
- **SELL:** Red circle (`red` fill, `darkred` edge)
- **Size:** 18pt with 3pt edge width
- **Position:** Exact trade price at trade time

### 3. Vertical Lines
- **Style:** Dashed, full chart height
- **BUY:** Green dashed (`g--`)
- **SELL:** Red dashed (`r--`)
- **Alpha:** 0.5
- **Z-order:** 4

### 4. Annotations
- **BUY box:** Light green fill, green border
  - Content: `BUY\n{time}\n@{price}\nx{qty}\n{trigger}`
- **SELL box:** Light coral fill, red border
  - Content: `SELL\n{time}\n@{price}\n{trigger}`
- **PnL label:** White box at top of chart
  - Content: `PnL: {value:+.2f} SAR`
  - Color: Green (profit) / Red (loss)

### 5. Entry Zone Lines
- **Style:** Purple dotted lines (`:`)
- **Width:** 3pt
- **Upper/lower:** From `picks_analysis.json`
- **No shading** (removed pink background)

### 6. VWAP Line
- **Color:** Orange
- **Style:** Dashed (`--`)
- **Calculation:** Mean of first 30 minutes of price data

### 7. Activity Band (Light Blue)
- **Based on:** `real` boolean field from websocket log
- **Logic:** 
  - More `real=true` ticks in window = more market activity
  - Band width proportional to real tick ratio
  - Formula: `base_width * (1.0 + real_ratio * 2.0)`
- **Purpose:** Proxy for volume/activity without actual volume data

---

## Calculation Order

1. Load price data (with `real` flags)
2. Load trades (status at column **10**)
3. Load entry zones from picks
4. Calculate trade pairs (BUY→SELL) and PnL
5. **Set y-limits first** (before plotting anything)
6. Draw activity band
7. Draw price line and VWAP
8. Draw entry zone lines
9. Draw trades (vertical lines, circles, annotations, PnL labels)

**Critical:** Y-limits must be set BEFORE plotting trades to ensure PnL labels at top are visible.

---

## Script Location

**Main script:** `/home/mino/.openclaw-mino/workspace/Charts/generate_v7.py`

**Usage:**
```bash
cd /home/mino/.openclaw-mino/workspace/Charts
python3 generate_v7.py
```

**Output:** `/home/mino/.openclaw-mino/workspace/Charts/combined_today_v7.png`

---

*Updated: June 16, 2026*
