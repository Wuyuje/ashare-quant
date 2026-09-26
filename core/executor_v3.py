#!/usr/bin/env python3
"""A股模拟交易执行器 v3 — 分批止盈优化版
基于 200只验证 + 组合回测:
  胜率 70.0% | 中位收益 +13.9% | 盈亏比 1.54
  组合收益 +15.91% | 回撤 -4.4% | 收益/回撤 3.62

配置:
  选股: v4评分 ≥ 60
  持仓: 10只（单仓9.5%）
  卖出: 止盈8%卖半 / 15%再卖半 / 移动止盈(峰6%回撤4%) / 止损-7% / 最长20天
  择时: 默认关（可开）
"""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, index_klines, quote_tencent
from scorer_v4 import score_v4
from scorer_v5 import sell_signal
from strategy_v3 import market_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(ROOT, "logs", "executor.log")
STATE = os.path.join(DATA, "portfolio.json")
CACHE = os.path.join(DATA, "stock_list.json")
os.makedirs(DATA, exist_ok=True)
os.makedirs(os.path.dirname(LOG), exist_ok=True)

# ═══════════ 优化配置（回测驱动） ═══════════
CFG = {
    "init_cash": 100000,
    "max_positions": 10,       # 10只分散（回撤-4.4%）
    "pos_pct": 0.095,          # 单只9.5%
    "buy_score": 60,           # v4评分门槛
    "use_market": False,       # ⭐ 择时默认关（回测显示收益更高）
    "min_amount": 5e8,         # 成交额≥5亿
    "min_price": 3,
    "max_price": 500,
    "screen_limit": 300,
    "interval": 300,           # 扫描间隔
    "hold_days": 20,           # 最长持有
    "min_hold_min": 2,         # 最短持有(分钟) — 防同轮反复交易
}

# ══════ 卖出参数（分批止盈） ══════
SELL = {
    "stop_loss": -7,           # 止损-7%
    "trail": 4,                # 移动止盈：回撤4%触发
    "trail_start": 6,          # 峰值6%后才启动移动止盈
    "partial": True,           # 启用分批止盈
    "tp1": 8,                  # 止盈一档8%卖一半
    "tp2": 15,                 # 止盈二档15%再卖一半
    "max_hold": 20,            # 最长20天
    "trend_hold": False,
}
COST = {"commission":0.00025, "stamp":0.0005, "slippage":0.0015, "min_fee":5}

def log(m):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except Exception: pass

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"cash": CFG["init_cash"], "positions": {}, "trades": [],
                "created": int(time.time()), "last_scan": 0}

def save_state(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)

def is_trading_time():
    now = datetime.datetime.now()
    if now.weekday() >= 5: return False
    t = now.hour * 60 + now.minute
    return (9*60+25 <= t <= 11*60+32) or (12*60+55 <= t <= 15*60+5)

def get_stocks():
    try:
        d = json.load(open(CACHE))
        if time.time() - d.get("t", 0) < 3600 and d.get("stocks"):
            return d["stocks"]
    except Exception: pass
    st = all_stocks(max_pages=30)
    if st:
        json.dump({"t": int(time.time()), "stocks": st}, open(CACHE, "w"), ensure_ascii=False)
    return st

def buy_cost(px, q):
    g = px*q
    return g + max(COST["min_fee"], g*COST["commission"]) + g*COST["slippage"]
def sell_net(px, q):
    g = px*q
    return g - max(COST["min_fee"], g*COST["commission"]) - g*COST["stamp"] - g*COST["slippage"]

# ═══════════ 主逻辑 ═══════════
def scan_and_trade():
    st = load_state()
    positions = st.get("positions") or {}

    # ① 大盘择时（可选）
    can_buy = True
    if CFG["use_market"]:
        bm = index_klines("sh000300", 300)
        if bm:
            ms = market_state(bm, len(bm)-1)
            if ms == "bear":
                can_buy = False
                log(f"⛔ 大盘熊市，暂停开仓")

    # ② 卖出处理（用 sell_signal）
    quotes = quote_tencent(list(positions.keys())) if positions else {}
    now_ts = time.time()
    for code in list(positions.keys()):
        pos = positions[code]
        # 最短持有保护
        if now_ts - pos.get("open_ts", 0) < CFG["min_hold_min"]*60: continue
        q = None
        for k, v in quotes.items():
            if v.get("code") == code: q = v; break
        if not q: continue
        cur = q["price"]
        # 构造K线序列供 sell_signal 判断
        try:
            k = klines(code, 60)
            if not k: continue
            # 追加当前价作为最新K线
            k.append({"close": cur, "high": cur, "low": cur, "open": cur, "vol": 0})
            i = len(k) - 1
            pos["peak"] = max(pos.get("peak", pos["px"]), cur)
            reason, ratio = sell_signal(k, i, pos, SELL)
        except Exception:
            reason, ratio = None, 0

        if reason:
            qty = pos["qty"]
            sell_qty = int(qty * ratio / 100) * 100
            if sell_qty < 100: sell_qty = qty
            gross = cur * sell_qty
            net = sell_net(cur, sell_qty)
            cost_part = pos["cost"] * (sell_qty / qty)
            st["cash"] = round(st["cash"] + net, 2)
            ret = (net - cost_part) / cost_part * 100
            st.setdefault("trades", []).append({
                "code": code, "name": pos.get("name"), "side": "SELL",
                "price": round(cur,2), "qty": sell_qty, "amount": round(gross,2),
                "ret": round(ret,2), "reason": reason,
                "hold_days": round((now_ts-pos["open_ts"])/86400,1),
                "ts": int(now_ts),
                "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
            })
            if ratio >= 1.0:
                del positions[code]
                log(f"🔴 卖出 {pos.get('name')}({code}) {sell_qty}股 @{cur:.2f} 收益{ret:+.2f}% 因{reason}")
            else:
                pos["qty"] -= sell_qty
                pos["cost"] -= cost_part
                if "一档" in reason: pos["partial1"] = True
                else: pos["partial2"] = True
                if pos["qty"] < 100: del positions[code]
                log(f"🟡 部分卖出 {pos.get('name')}({code}) {sell_qty}股 @{cur:.2f} 收益{ret:+.2f}% 因{reason}")

    # ③ 买入
    slots = CFG["max_positions"] - len(positions)
    if can_buy and slots > 0 and st["cash"] > 5000:
        stocks = get_stocks()
        cand = []
        for s in stocks:
            try:
                p = float(s.get("price") or 0)
                amt = float(s.get("amount") or 0)
                nm = s.get("name") or ""
                if p < CFG["min_price"] or p > CFG["max_price"]: continue
                if amt < CFG["min_amount"]: continue
                if "ST" in nm or "退" in nm: continue
                cand.append(s)
            except Exception: continue
        cand.sort(key=lambda x: -(float(x.get("amount") or 0)))
        cand = cand[:CFG["screen_limit"]]

        picks = []
        for s in cand:
            code = s["code"]
            if code in positions: continue
            try:
                k = klines(code, 120)
                if not k or len(k) < 65: continue
                r = score_v4(k)
                if not r: continue
                sc = r[0]
                if sc >= CFG["buy_score"]:
                    picks.append({"code": code, "name": s["name"],
                                  "price": float(s["price"]), "score": sc})
            except Exception: continue
            time.sleep(0.04)
            if len(picks) >= slots * 3: break

        picks.sort(key=lambda x: -x["score"])
        for p in picks[:slots]:
            budget = min(st["cash"] * CFG["pos_pct"], st["cash"] - 1000)
            if budget < 3000: break
            price = p["price"]
            qty = int(budget / price / 100) * 100
            if qty < 100: continue
            gross = price * qty
            fee = max(5, gross*COST["commission"]) + gross*COST["slippage"]
            total = gross + fee
            if total > st["cash"]: continue
            st["cash"] = round(st["cash"] - total, 2)
            positions[p["code"]] = {
                "name": p["name"], "qty": qty, "px": round(price,2),
                "cost": total, "open_ts": now_ts, "score": p["score"],
                "peak": price, "partial1": False, "partial2": False,
            }
            st.setdefault("trades", []).append({
                "code": p["code"], "name": p["name"], "side": "BUY",
                "price": round(price,2), "qty": qty, "amount": round(gross,2),
                "score": p["score"], "reason": "选股买入",
                "ts": int(now_ts),
                "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
            })
            log(f"🟢 买入 {p['name']}({p['code']}) {qty}股 @{price:.2f} 评分{p['score']:.0f}")

    st["positions"] = positions
    st["last_scan"] = int(now_ts)
    save_state(st)

def stats():
    st = load_state()
    trades = st.get("trades") or []
    sells = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sells if t.get("ret",0) > 0]
    pos = st.get("positions") or {}
    mv = 0
    if pos:
        q = quote_tencent(list(pos.keys()))
        for code, p in pos.items():
            for k, v in q.items():
                if v.get("code") == code:
                    mv += v["price"] * p["qty"]
    total = st["cash"] + mv
    return {
        "cash": round(st["cash"],2), "market_value": round(mv,2),
        "total": round(total,2), "init": CFG["init_cash"],
        "profit": round(total - CFG["init_cash"],2),
        "profit_pct": round((total-CFG["init_cash"])/CFG["init_cash"]*100,2),
        "positions": len(pos), "sells": len(sells),
        "wins": len(wins), "losses": len(sells)-len(wins),
        "winrate": round(len(wins)/len(sells)*100,1) if sells else 0,
        "last_scan": st.get("last_scan",0),
    }

def main():
    log(f"🚀 A股模拟 v3 分批止盈版 | 资金{CFG['init_cash']:,} | "
        f"{CFG['max_positions']}仓 | 单仓{CFG['pos_pct']*100:.1f}% | "
        f"评分≥{CFG['buy_score']} | 止盈{SELL['tp1']}/{SELL['tp2']}% | "
        f"移动止盈(峰{SELL['trail_start']}%回撤{SELL['trail']}%) | "
        f"止损{SELL['stop_loss']}% | 择时{'开' if CFG['use_market'] else '关'}")
    while True:
        try:
            if is_trading_time():
                scan_and_trade()
            time.sleep(CFG["interval"])
        except Exception as e:
            log(f"❌ {repr(e)[:120]}")
            time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        scan_and_trade(); print(json.dumps(stats(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
    else:
        main()
