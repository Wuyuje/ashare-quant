#!/usr/bin/env python3
"""A股模拟交易执行器 v2 — 优化配置
基于 200只大样本验证 + 组合回测结果:
  最优: 持10只 + 大盘择时 + 评分60 + 持有10天 + 移动止盈3%
  回测: 收益+7.30% 回撤-3.1%（收益/回撤=2.35）
"""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, index_klines, quote_tencent
from scorer_v4 import score_v4
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
    "init_cash": 100000,        # 初始资金 10万
    "max_positions": 10,        # ⭐ 持10只（分散，回撤-3.1%）
    "pos_pct": 0.095,           # 单只 9.5%
    "buy_score": 60,            # ⭐ v4评分门槛
    "sell_score": 40,
    "stop_loss": -7.0,          # 止损 -7%
    "trail": 3.0,               # ⭐ 移动止盈（峰值回撤3%）
    "max_hold_days": 10,        # 持有10天
    "min_amount": 5e8,          # 最小成交额5亿（避免小盘）
    "min_price": 3,
    "max_price": 500,
    "screen_limit": 300,        # 扫描前300只
    "interval": 300,            # 扫描间隔
    "use_market": True,         # ⭐ 大盘择时（熊市空仓）
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
    now = datetime.datetime.now()
    if now.weekday() >= 5: return False
    t = now.hour * 60 + now.minute
    return (9*60+25 <= t <= 11*60+32) or (12*60+55 <= t <= 15*60+5)

def get_stocks():
    """带缓存的股票列表（1小时）"""
    try:
        d = json.load(open(CACHE))
        if time.time() - d.get("t", 0) < 3600 and d.get("stocks"):
            return d["stocks"]
    except Exception: pass
    st = all_stocks(max_pages=30)
    if st:
        json.dump({"t": int(time.time()), "stocks": st}, open(CACHE, "w"),
                  ensure_ascii=False)
    return st

# ═══════════ 主逻辑 ═══════════
def scan_and_trade():
    st = load_state()
    positions = st.get("positions") or {}

    # ① 大盘择时
    bm = index_klines("sh000300", 300)
    if CFG["use_market"] and bm:
        ms = market_state(bm, len(bm)-1)
        if ms == "bear":
            log(f"⛔ 大盘熊市({ms})，暂停开仓（现有持仓继续管理）")
            can_buy = False
        else:
            can_buy = True
    else:
        can_buy = True

    # ② 处理持仓（卖出检查）
    quotes = quote_tencent(list(positions.keys())) if positions else {}
    to_sell = []
    for code, pos in positions.items():
        q = None
        for k, v in quotes.items():
            if v.get("code") == code: q = v; break
        if not q: continue
        cur = q["price"]
        ret = (cur - pos["cost"]) / pos["cost"] * 100
        held = (time.time() - pos["open_ts"]) / 86400
        # 移动止盈
        peak = max(pos.get("peak", pos["cost"]), cur)
        peak_ret = (peak - pos["cost"]) / pos["cost"] * 100
        reason = None
        if ret <= CFG["stop_loss"]:
            reason = f"止损({ret:.1f}%)"
        elif CFG["trail"] and peak_ret >= 3 and (peak_ret - ret) >= CFG["trail"]:
            reason = f"移动止盈(峰{peak_ret:.1f}%→{ret:.1f}%)"
        elif held >= CFG["max_hold_days"]:
            reason = f"到期({held:.1f}天)"
        else:
            # 评分检查
            try:
                k = klines(code, 120)
                if k and len(k) >= 60:
                    r = score_v4(k)
                    if r and r[0] < CFG["sell_score"]:
                        reason = f"评分降({r[0]:.0f})"
            except Exception: pass
        # 更新峰值
        if cur > pos.get("peak", 0):
            positions[code]["peak"] = cur
        if reason:
            to_sell.append((code, cur, ret, reason, q.get("name") or code))

    for code, price, ret, reason, name in to_sell:
        pos = positions[code]
        qty = pos["qty"]
        gross = price * qty
        fee = max(5, gross*0.00025) + gross*0.0005 + gross*0.0015   # 佣金+印花税+滑点
        net = gross - fee
        st["cash"] = round(st["cash"] + net, 2)
        st.setdefault("trades", []).append({
            "code": code, "name": name, "side": "SELL",
            "price": round(price,2), "qty": qty, "amount": round(gross,2),
            "fee": round(fee,2), "ret": round(ret,2), "reason": reason,
            "hold_days": round((time.time()-pos["open_ts"])/86400,1),
            "ts": int(time.time()),
            "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
        })
        del positions[code]
        log(f"🔴 卖出 {name}({code}) {qty}股 @{price:.2f} 收益{ret:+.2f}% 因{reason}")

    # ③ 买入（如果允许）
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
                sc, detail = r
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
            fee = max(5, gross*0.00025) + gross*0.0015
            total = gross + fee
            if total > st["cash"]: continue
            st["cash"] = round(st["cash"] - total, 2)
            positions[p["code"]] = {
                "name": p["name"], "qty": qty, "cost": round(price,2),
                "open_ts": time.time(), "score": p["score"], "peak": price,
            }
            st.setdefault("trades", []).append({
                "code": p["code"], "name": p["name"], "side": "BUY",
                "price": round(price,2), "qty": qty, "amount": round(gross,2),
                "fee": round(fee,2), "score": p["score"], "reason": "选股买入",
                "ts": int(time.time()),
                "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
            })
            log(f"🟢 买入 {p['name']}({p['code']}) {qty}股 @{price:.2f} 评分{p['score']:.0f}")

    st["positions"] = positions
    st["last_scan"] = int(time.time())
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
    log(f"🚀 A股模拟 v2 优化配置 | 资金{CFG['init_cash']:,} | "
        f"{CFG['max_positions']}仓 | 单仓{CFG['pos_pct']*100:.0f}% | "
        f"评分≥{CFG['buy_score']} | 持有{CFG['max_hold_days']}天 | "
        f"移动止盈{CFG['trail']}% | 大盘择时{'开' if CFG['use_market'] else '关'}")
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
