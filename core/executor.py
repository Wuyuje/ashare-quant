#!/usr/bin/env python3
"""A股模拟交易执行器 — 选股→模拟买卖→持仓管理→结算
独立模块，不依赖其他系统
设计: 与真实交易同一套逻辑，后期可直接切换 miniQMT
"""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, quote_tencent
from screener import score_stock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(ROOT, "logs", "executor.log")
STATE = os.path.join(DATA, "portfolio.json")
os.makedirs(DATA, exist_ok=True)
os.makedirs(os.path.dirname(LOG), exist_ok=True)

# ═══════════ 策略参数 ═══════════
CFG = {
    "init_cash": 100000,        # 初始资金 10万
    "max_positions": 5,         # 最多5只
    "pos_pct": 0.18,            # 单只最多18%仓位
    "buy_score": 75,            # 买入评分门槛
    "sell_score": 45,           # 评分低于此卖出
    "stop_loss": -7.0,          # 止损 -7%
    "take_profit": 15.0,        # 止盈 +15%
    "max_hold_days": 10,        # 最长持有10天
    "min_amount": 1e8,          # 最小成交额1亿
    "min_price": 3,
    "max_price": 500,
    "screen_limit": 200,        # 每次扫描前200只
    "interval": 300,            # 扫描间隔(秒) — 交易时段内
}

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
    """A股交易时段: 周一~周五 9:30-11:30, 13:00-15:00"""
    now = datetime.datetime.now()
    if now.weekday() >= 5: return False
    t = now.hour * 60 + now.minute
    return (9*60+25 <= t <= 11*60+32) or (12*60+55 <= t <= 15*60+5)

# ═══════════ 扫描 + 决策 ═══════════
def scan_and_trade():
    st = load_state()
    positions = st.get("positions") or {}

    # ① 拉全市场
    stocks = all_stocks()
    if not stocks:
        log("⚠️ 无法获取股票列表")
        return
    # 过滤
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
        except Exception:
            continue
    cand.sort(key=lambda x: -(float(x.get("amount") or 0)))
    cand = cand[:CFG["screen_limit"]]

    # ② 检查持仓（先处理卖出）
    quotes = quote_tencent([c for c in positions.keys()])
    to_sell = []
    for code, pos in positions.items():
        q = None
        for k, v in quotes.items():
            if v.get("code") == code: q = v; break
        if not q: continue
        cur = q["price"]
        ret = (cur - pos["cost"]) / pos["cost"] * 100
        held = (time.time() - pos["open_ts"]) / 86400
        reason = None
        if ret <= CFG["stop_loss"]: reason = f"止损({ret:.1f}%)"
        elif ret >= CFG["take_profit"]: reason = f"止盈({ret:.1f}%)"
        elif held >= CFG["max_hold_days"]: reason = f"到期({held:.1f}天)"
        # 评分检查
        if not reason:
            try:
                k = klines(code, 120)
                if k and len(k) >= 60:
                    r = score_stock(k)
                    if r and r[0] < CFG["sell_score"]:
                        reason = f"评分降({r[0]:.0f})"
            except Exception:
                pass
        if reason:
            to_sell.append((code, cur, ret, reason, q.get("name") or code))

    for code, price, ret, reason, name in to_sell:
        pos = positions[code]
        qty = pos["qty"]
        # A股: 卖出免印花税(2026新规? 实际卖出收0.05%) + 佣金万3
        gross = price * qty
        fee = max(5, gross * 0.0003) + gross * 0.0005   # 佣金+印花税
        net = gross - fee
        st["cash"] = round(st["cash"] + net, 2)
        st.setdefault("trades", []).append({
            "code": code, "name": name, "side": "SELL",
            "price": round(price, 2), "qty": qty,
            "amount": round(gross, 2), "fee": round(fee, 2),
            "ret": round(ret, 2), "reason": reason,
            "hold_days": round((time.time()-pos["open_ts"])/86400, 1),
            "ts": int(time.time()),
            "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
        })
        del positions[code]
        log(f"🔴 卖出 {name}({code}) {qty}股 @{price:.2f} 收益{ret:+.2f}% 因{reason}")

    # ③ 找买入（评分最高的）
    slots = CFG["max_positions"] - len(positions)
    if slots > 0 and st["cash"] > 5000:
        picks = []
        for s in cand:
            code = s["code"]
            if code in positions: continue
            try:
                k = klines(code, 120)
                if not k or len(k) < 60: continue
                r = score_stock(k, s)
                if not r: continue
                sc, detail = r
                if sc >= CFG["buy_score"]:
                    picks.append({"code": code, "name": s["name"], "price": float(s["price"]),
                                  "score": sc, "detail": detail})
            except Exception:
                continue
            time.sleep(0.06)
            if len(picks) >= slots * 2: break

        picks.sort(key=lambda x: -x["score"])
        for p in picks[:slots]:
            budget = min(st["cash"] * CFG["pos_pct"], st["cash"] - 1000)
            if budget < 2000: break
            price = p["price"]
            qty = int(budget / price / 100) * 100      # A股100股整数倍
            if qty < 100: continue
            gross = price * qty
            fee = max(5, gross * 0.0003)               # 买入佣金
            total = gross + fee
            if total > st["cash"]: continue
            st["cash"] = round(st["cash"] - total, 2)
            positions[p["code"]] = {
                "name": p["name"], "qty": qty, "cost": round(price, 2),
                "open_ts": time.time(), "score": p["score"],
                "open_price": round(price, 2),
            }
            st.setdefault("trades", []).append({
                "code": p["code"], "name": p["name"], "side": "BUY",
                "price": round(price, 2), "qty": qty,
                "amount": round(gross, 2), "fee": round(fee, 2),
                "score": p["score"], "reason": "选股买入",
                "ts": int(time.time()),
                "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
            })
            log(f"🟢 买入 {p['name']}({p['code']}) {qty}股 @{price:.2f} 评分{p['score']:.0f}")

    st["positions"] = positions
    st["last_scan"] = int(time.time())
    save_state(st)

# ═══════════ 统计 ═══════════
def stats():
    st = load_state()
    trades = st.get("trades") or []
    sells = [t for t in trades if t["side"] == "SELL"]
    buys = [t for t in trades if t["side"] == "BUY"]
    wins = [t for t in sells if t.get("ret", 0) > 0]
    losses = [t for t in sells if t.get("ret", 0) <= 0]
    pos = st.get("positions") or {}
    # 持仓市值
    mv = 0
    if pos:
        q = quote_tencent(list(pos.keys()))
        for code, p in pos.items():
            for k, v in q.items():
                if v.get("code") == code:
                    mv += v["price"] * p["qty"]
    return {
        "cash": st["cash"],
        "market_value": round(mv, 2),
        "total": round(st["cash"] + mv, 2),
        "init": CFG["init_cash"],
        "profit": round(st["cash"] + mv - CFG["init_cash"], 2),
        "profit_pct": round((st["cash"] + mv - CFG["init_cash"]) / CFG["init_cash"] * 100, 2),
        "positions": len(pos),
        "buys": len(buys), "sells": len(sells),
        "wins": len(wins), "losses": len(losses),
        "winrate": round(len(wins)/len(sells)*100, 1) if sells else 0,
        "last_scan": st.get("last_scan", 0),
    }

def main():
    log(f"🚀 A股模拟交易启动 初始资金 {CFG['init_cash']:,} 最多{CFG['max_positions']}仓 评分门槛{CFG['buy_score']}")
    while True:
        try:
            if is_trading_time():
                scan_and_trade()
            else:
                pass    # 非交易时段不操作
            time.sleep(CFG["interval"])
        except Exception as e:
            log(f"❌ {repr(e)[:120]}")
            time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        scan_and_trade()
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
    else:
        main()
