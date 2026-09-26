#!/usr/bin/env python3
"""A股选股引擎 — 多因子选股
独立模块，不依赖其他系统
"""
import json, os, sys, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, quote_tencent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)

def ema(arr, n):
    if len(arr) < n: return None
    k = 2/(n+1); e = sum(arr[:n])/n
    for x in arr[n:]: e = x*k + e*(1-k)
    return e

def ma(arr, n):
    if len(arr) < n: return None
    return sum(arr[-n:])/n

def rsi(closes, n=14):
    if len(closes) < n+1: return 50
    g = l = 0.0
    for i in range(1, n+1):
        d = closes[i]-closes[i-1]
        g += max(d,0); l += max(-d,0)
    ag, al = g/n, l/n
    for i in range(n+1, len(closes)):
        d = closes[i]-closes[i-1]
        ag = (ag*(n-1)+max(d,0))/n
        al = (al*(n-1)+max(-d,0))/n
    if al == 0: return 100
    return 100-100/(1+ag/al)

def atr(k, n=14):
    if len(k) < n+1: return 0
    trs = []
    for i in range(len(k)-n, len(k)):
        h, l, pc = k[i]["high"], k[i]["low"], k[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/len(trs) if trs else 0

def vol_ratio(k, n=5, m=20):
    """量比: 近n日均量 / 近m日均量"""
    if len(k) < m: return 1
    vn = sum(x["vol"] for x in k[-n:])/n
    vm = sum(x["vol"] for x in k[-m:])/m
    return vn/vm if vm else 1

def momentum(k, n=20):
    if len(k) < n+1: return 0
    return (k[-1]["close"] - k[-n-1]["close"])/k[-n-1]["close"]*100

# ═══════════════ 单只股票评分 ═══════════════
def score_stock(k, st=None):
    """返回 (总分, 明细)  总分 0-100"""
    if not k or len(k) < 60:
        return None
    c = [x["close"] for x in k]
    detail = {}
    score = 0.0

    # ① 趋势 (25分): 均线多头
    ma5, ma10, ma20, ma60 = ma(c,5), ma(c,10), ma(c,20), ma(c,60)
    if ma5 and ma10 and ma20 and ma60:
        t = 0
        if ma5 > ma10: t += 1
        if ma10 > ma20: t += 1
        if ma20 > ma60: t += 1
        if c[-1] > ma20: t += 1
        score += t/4*25
        detail["trend"] = round(t/4*100, 1)

    # ② 动量 (20分): 20日涨幅
    mom = momentum(k, 20)
    m = max(0, min(20, (mom+10)/30*20))     # -10%~+20% 映射到 0-20
    score += m
    detail["momentum"] = round(m, 1)
    detail["mom20"] = round(mom, 2)

    # ③ RSI (15分): 50-70 最佳（强势不超买）
    r = rsi(c, 14)
    if 50 <= r <= 70: rs = 15
    elif 45 <= r < 50: rs = 10
    elif 70 < r <= 80: rs = 8
    elif 40 <= r < 45: rs = 5
    else: rs = 2
    score += rs
    detail["rsi"] = round(r, 1)

    # ④ 量能 (15分): 放量优先
    vr = vol_ratio(k)
    vs = 15 if vr >= 1.5 else (12 if vr >= 1.2 else (8 if vr >= 1.0 else (4 if vr >= 0.8 else 1)))
    score += vs
    detail["vol_ratio"] = round(vr, 2)

    # ⑤ 波动 (10分): 适中最好
    a = atr(k, 14)
    if c[-1] > 0:
        ap = a/c[-1]*100
        vs2 = 10 if 2 <= ap <= 5 else (6 if ap < 2 else 4)
        score += vs2
        detail["atr_pct"] = round(ap, 2)

    # ⑥ 位置 (15分): 距60日高点
    hi60 = max(x["high"] for x in k[-60:])
    if hi60 > 0:
        pos = c[-1]/hi60
        ps = 15 if pos >= 0.95 else (12 if pos >= 0.90 else (8 if pos >= 0.80 else 3))
        score += ps
        detail["pos60"] = round(pos*100, 1)

    return round(score, 1), detail

# ═══════════════ 全市场选股 ═══════════════
def screen(limit=300, min_price=3, max_price=300, min_amount=1e8,
           exclude_st=True, exclude_new=True, top_n=20):
    """全市场选股
    limit: 检查前N只（按成交额排序）
    min_amount: 最小成交额（默认1亿）
    """
    print(f"📊 拉取全市场列表...", flush=True)
    stocks = all_stocks()
    print(f"  共 {len(stocks)} 只", flush=True)

    # ① 基础过滤
    cand = []
    for s in stocks:
        try:
            p = float(s.get("price") or 0)
            amt = float(s.get("amount") or 0)
            nm = s.get("name") or ""
            if p < min_price or p > max_price: continue
            if amt < min_amount: continue
            if exclude_st and ("ST" in nm or "退" in nm or "*" in nm): continue
            if exclude_new and ("N" == nm[:1] or "C" == nm[:1]): continue
            cand.append(s)
        except Exception:
            continue

    # ② 按成交额排序，取前 limit
    cand.sort(key=lambda x: -(float(x.get("amount") or 0)))
    cand = cand[:limit]
    print(f"  过滤后 {len(cand)} 只（检查中）", flush=True)

    # ③ 逐只评分
    results = []
    for i, s in enumerate(cand):
        code = s["code"]
        try:
            k = klines(code, 120)
            if not k or len(k) < 60: continue
            r = score_stock(k, s)
            if not r: continue
            sc, detail = r
            results.append({
                "code": code, "name": s["name"], "price": s["price"],
                "chg_pct": s.get("chg_pct"), "amount": s.get("amount"),
                "score": sc, "detail": detail,
                "pe": s.get("pe"), "pb": s.get("pb"),
                "turnover": s.get("turnover"),
                "vol_ratio": s.get("vol_ratio"),
            })
        except Exception:
            continue
        if (i+1) % 50 == 0:
            print(f"    已检查 {i+1}/{len(cand)}", flush=True)
        time.sleep(0.08)     # 控速防限流

    results.sort(key=lambda x: -x["score"])
    return results[:top_n]

if __name__ == "__main__":
    print("=== A股选股测试 ===\n")
    top = screen(limit=100, top_n=10)
    print(f"\n🏆 Top {len(top)}:")
    print(f"{'代码':8s} {'名称':10s} {'现价':>8s} {'涨跌%':>7s} {'评分':>6s}  明细")
    for r in top:
        print(f"{r['code']:8s} {r['name']:10s} {r['price']:>8.2f} {r['chg_pct']:>7.2f} {r['score']:>6.1f}  {r['detail']}")
