#!/usr/bin/env python3
"""A股策略优化 — 参数网格搜索 + 逐项改进验证
基准: 随机95只 = 胜率42.4% 收益-2.18%
目标: 找到有正期望的参数组合
"""
import json, os, sys, time, random, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, index_klines
from screener import ma, rsi, atr, momentum, vol_ratio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)

# ═══════════ 成本 ═══════════
COST = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.0015, "min_fee": 5}

def buy_cost(price, qty):
    g = price*qty
    return g + max(COST["min_fee"], g*COST["commission"]) + g*COST["slippage"]
def sell_net(price, qty):
    g = price*qty
    return g - max(COST["min_fee"], g*COST["commission"]) - g*COST["stamp"] - g*COST["slippage"]

def limit_up(k, i):
    if i<=0: return False
    p=k[i-1]["close"]
    return p>0 and (k[i]["close"]-p)/p*100 >= 9.8
def limit_down(k, i):
    if i<=0: return False
    p=k[i-1]["close"]
    return p>0 and (k[i]["close"]-p)/p*100 <= -9.8

# ═══════════ 评分函数（可配置） ═══════════
def score_v2(k, cfg):
    """可配置评分
    cfg: buy_mode='breakout'(追强势) | 'pullback'(回调买)
    """
    if not k or len(k) < 60: return None
    c = [x["close"] for x in k]
    ma5, ma10, ma20, ma60 = ma(c,5), ma(c,10), ma(c,20), ma(c,60)
    if not all([ma5, ma10, ma20, ma60]): return None
    sc = 0.0
    d = {}

    # 趋势
    t = sum([ma5>ma10, ma10>ma20, ma20>ma60, c[-1]>ma20])
    sc += t/4 * cfg.get("w_trend", 25)
    d["trend"] = t

    # 动量
    mom = momentum(k, 20)
    d["mom20"] = round(mom, 2)
    if cfg.get("buy_mode") == "pullback":
        # 回调买: 动量温和（0-8%最佳），太高不买
        m = 20 if 0 <= mom <= 8 else (10 if mom <= 15 else 0)
    else:
        m = max(0, min(20, (mom+10)/30*20))
    sc += m
    d["mom_score"] = round(m,1)

    # RSI
    r = rsi(c, 14)
    d["rsi"] = round(r,1)
    if cfg.get("buy_mode") == "pullback":
        # 回调买: RSI 40-60 最好
        rs = 15 if 40<=r<=60 else (10 if 35<=r<65 else 2)
    else:
        rs = 15 if 50<=r<=70 else (10 if 45<=r<50 else (8 if 70<r<=80 else 2))
    sc += rs

    # 量能
    vr = vol_ratio(k)
    d["volr"] = round(vr,2)
    vs = 15 if vr>=cfg.get("vol_min",1.5) else (12 if vr>=1.2 else (8 if vr>=1.0 else 2))
    sc += vs

    # 波动
    a = atr(k,14)/c[-1]*100 if c[-1] else 0
    d["atr"] = round(a,2)
    vs2 = 10 if 2<=a<=5 else (6 if a<2 else 3)
    sc += vs2

    # 位置
    hi60 = max(x["high"] for x in k[-60:])
    pos = c[-1]/hi60 if hi60 else 0
    d["pos60"] = round(pos*100,1)
    if cfg.get("buy_mode") == "pullback":
        # 回调买: 位置 70-92% 最好（回调中但没崩）
        ps = 15 if 0.70<=pos<=0.92 else (8 if pos>0.92 else 3)
    else:
        ps = 15 if pos>=0.95 else (12 if pos>=0.90 else (8 if pos>=0.80 else 3))
    sc += ps

    return round(sc,1), d

# ═══════════ 通用回测 ═══════════
def bt(code, name, cfg, days=250):
    k = klines(code, days)
    if not k or len(k) < 80: return None
    cash = 100000.0
    pos = None
    trades = []
    eq = []
    i = 60
    hold = cfg.get("hold", 5)
    bs = cfg.get("buy_score", 75)
    sl = cfg.get("stop_loss", -7)
    tp = cfg.get("take_profit", 15)
    trail = cfg.get("trail", None)     # 移动止盈: 从最高点回撤N%卖
    while i < len(k)-1:
        r = score_v2(k[:i+1], cfg)
        sc = r[0] if r else 0
        price = k[i]["close"]

        if pos is None:
            if sc >= bs:
                if limit_up(k, i): i+=1; continue
                qty = int(cash*0.95/price/100)*100
                if qty >= 100:
                    t = buy_cost(price, qty)
                    if t <= cash:
                        cash -= t
                        pos = {"i": i, "px": price, "qty": qty, "dt": k[i]["date"],
                               "sc": sc, "cost": t, "peak": price}
        else:
            ret = (price-pos["px"])/pos["px"]*100
            held = i-pos["i"]
            pos["peak"] = max(pos["peak"], price)
            peak_ret = (pos["peak"]-pos["px"])/pos["px"]*100
            reason = None
            if ret <= sl: reason = "止损"
            elif tp and ret >= tp: reason = "止盈"
            elif trail and peak_ret >= 3 and (peak_ret-ret) >= trail:
                reason = f"移动止盈(峰{peak_ret:.1f}%→{ret:.1f}%)"
            elif held >= hold: reason = "到期"
            elif sc < cfg.get("sell_score", 45): reason = "评分降"
            if reason:
                if limit_down(k, i): i+=1; continue
                net = sell_net(price, pos["qty"])
                cash += net
                rr = (net-pos["cost"])/pos["cost"]*100
                trades.append({"buy": pos["dt"], "sell": k[i]["date"],
                               "ret": round(rr,2), "gross": round(ret,2),
                               "held": held, "reason": reason})
                pos = None
        mv = pos["qty"]*price if pos else 0
        eq.append(cash+mv)
        i += 1
    if not trades: return None
    wins = [t for t in trades if t["ret"]>0]
    tot = sum(t["ret"] for t in trades)
    peak = eq[0] if eq else 100000
    mdd = 0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v-peak)/peak*100)
    return {"code": code, "name": name, "n": len(trades),
            "wins": len(wins), "wr": round(len(wins)/len(trades)*100,1),
            "ret": round(tot,2), "avg": round(tot/len(trades),2),
            "final": round(eq[-1],2) if eq else 100000,
            "pool": round((eq[-1]-100000)/100000*100,2) if eq else 0,
            "mdd": round(mdd,2),
            "avg_win": round(sum(t["ret"] for t in wins)/len(wins),2) if wins else 0,
            "avg_loss": round(sum(t["ret"] for t in trades if t["ret"]<=0)/max(1,len(trades)-len(wins)),2)}

def get_sample(n=60, seed=42, min_amt=5e8):
    """获取测试样本（固定随机种子，保证可比）"""
    st = all_stocks()
    cand = [s for s in st if s.get("amount") and float(s["amount"])>min_amt
            and "ST" not in (s.get("name") or "") and "退" not in (s.get("name") or "")]
    random.seed(seed)
    return random.sample(cand, min(n, len(cand)))

def batch_test(sample, cfg, label=""):
    rs = []
    for s in sample:
        try:
            x = bt(s["code"], s["name"], cfg)
            if x: rs.append(x)
        except Exception: pass
        time.sleep(0.04)
    if not rs: return None
    n = len(rs)
    tt = sum(x["n"] for x in rs)
    tw = sum(x["wins"] for x in rs)
    ap = sum(x["pool"] for x in rs)/n
    aw = sum(x["wr"] for x in rs)/n
    win = [x for x in rs if x["pool"]>0]
    mdd = sum(x["mdd"] for x in rs)/n
    return {"label": label, "stocks": n, "trades": tt,
            "wr": round(tw/tt*100,1) if tt else 0,
            "avg_wr": round(aw,1),
            "pool": round(ap,2),
            "win_stocks": len(win),
            "win_pct": round(len(win)/n*100,1),
            "mdd": round(mdd,1)}
