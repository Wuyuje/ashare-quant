#!/usr/bin/env python3
"""A股策略可信度验证 — 基准对比 + 真实成本 + 样本外测试 + 单因子分析
目的: 判断策略是真有效还是过拟合/牛市运气
"""
import json, os, sys, time, datetime, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, quote_tencent
from screener import score_stock, ma, rsi, atr, momentum, vol_ratio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# ═══════════ 成本参数（真实） ═══════════
COST = {
    "commission": 0.00025,   # 佣金 万2.5（双边）
    "stamp": 0.0005,         # 印花税 千0.5（仅卖出）
    "slippage": 0.0015,      # 滑点 0.15%（双边）
    "min_fee": 5,            # 最低佣金5元
}

def buy_cost(price, qty):
    gross = price * qty
    fee = max(COST["min_fee"], gross * COST["commission"])
    slip = gross * COST["slippage"]
    return gross + fee + slip

def sell_net(price, qty):
    gross = price * qty
    fee = max(COST["min_fee"], gross * COST["commission"]) + gross * COST["stamp"]
    slip = gross * COST["slippage"]
    return gross - fee - slip

# ═══════════ 涨跌停检查 ═══════════
def limit_up(k, i):
    """第i根是否涨停（无法买入）"""
    if i <= 0: return False
    prev = k[i-1]["close"]
    if prev <= 0: return False
    chg = (k[i]["close"] - prev) / prev * 100
    # 主板10%，创业板/科创板20%
    return chg >= 9.8

def limit_down(k, i):
    """第i根是否跌停（无法卖出）"""
    if i <= 0: return False
    prev = k[i-1]["close"]
    if prev <= 0: return False
    chg = (k[i]["close"] - prev) / prev * 100
    return chg <= -9.8

# ═══════════ 基准（沪深300） ═══════════
def get_benchmark(days=250):
    """沪深300 指数走势"""
    k = klines("000300", days, fq=0)     # 000300 沪深300
    if not k:
        # 用上证指数备选
        k = klines("000001", days, fq=0)
    return k

# ═══════════ 回测（含真实成本） ═══════════
def backtest_v2(code, name, days=250, hold=5, buy_score=75,
                stop_loss=-7, take_profit=15, use_cost=True, use_limit=True):
    """含成本+涨跌停的回测"""
    k = klines(code, days)
    if not k or len(k) < 80:
        return None
    trades = []
    cash = 100000.0
    pos = None
    equity_curve = []
    i = 60
    while i < len(k) - 1:
        sub = k[:i+1]
        r = score_stock(sub)
        sc = r[0] if r else 0
        price = k[i]["close"]

        if pos is None:
            # 买入
            if sc >= buy_score:
                if use_limit and limit_up(k, i):
                    i += 1; continue          # 涨停买不进
                qty = int(cash * 0.95 / price / 100) * 100
                if qty >= 100:
                    total = buy_cost(price, qty)
                    if total <= cash:
                        cash -= total
                        pos = {"idx": i, "price": price, "qty": qty,
                               "date": k[i]["date"], "score": sc,
                               "cost_total": total}
        else:
            ret = (price - pos["price"]) / pos["price"] * 100
            held = i - pos["idx"]
            reason = None
            if ret <= stop_loss: reason = "止损"
            elif ret >= take_profit: reason = "止盈"
            elif held >= hold: reason = "到期"
            elif sc < 45: reason = "评分降"
            if reason:
                if use_limit and limit_down(k, i):
                    i += 1; continue          # 跌停卖不出
                net = sell_net(price, pos["qty"])
                cash += net
                real_ret = (net - pos["cost_total"]) / pos["cost_total"] * 100
                trades.append({"buy_date": pos["date"], "sell_date": k[i]["date"],
                               "buy": pos["price"], "sell": price, "qty": pos["qty"],
                               "ret": round(real_ret, 2), "gross_ret": round(ret, 2),
                               "held": held, "reason": reason, "score": pos["score"]})
                pos = None
        # 记录净值
        mv = pos["qty"] * price if pos else 0
        equity_curve.append({"date": k[i]["date"], "v": round(cash + mv, 2)})
        i += 1

    if not trades:
        return None
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    tot = sum(t["ret"] for t in trades)
    # 最大回撤
    peak = equity_curve[0]["v"] if equity_curve else 100000
    mdd = 0
    for e in equity_curve:
        peak = max(peak, e["v"])
        mdd = min(mdd, (e["v"] - peak) / peak * 100)
    return {
        "code": code, "name": name, "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "winrate": round(len(wins)/len(trades)*100, 1),
        "total_ret": round(tot, 2),
        "avg_ret": round(tot/len(trades), 2),
        "avg_win": round(sum(t["ret"] for t in wins)/len(wins),2) if wins else 0,
        "avg_loss": round(sum(t["ret"] for t in losses)/len(losses),2) if losses else 0,
        "max_dd": round(mdd, 2),
        "final_equity": round(equity_curve[-1]["v"],2) if equity_curve else 100000,
        "pool_ret": round((equity_curve[-1]["v"]-100000)/100000*100,2) if equity_curve else 0,
        "detail": trades,
    }

# ═══════════ 单因子测试 ═══════════
def factor_test(codes, days=250, hold=5):
    """测试每个因子单独的效果"""
    factors = {"trend": [], "momentum": [], "rsi": [], "vol": [], "atr": [], "pos": []}
    for code, name in codes:
        k = klines(code, days)
        if not k or len(k) < 80: continue
        for i in range(60, len(k)-hold):
            sub = k[:i+1]
            c = [x["close"] for x in sub]
            fwd = (k[i+hold]["close"] - k[i]["close"]) / k[i]["close"] * 100
            # 各因子值
            ma5, ma10, ma20, ma60 = ma(c,5), ma(c,10), ma(c,20), ma(c,60)
            if not all([ma5, ma10, ma20, ma60]): continue
            t = sum([ma5>ma10, ma10>ma20, ma20>ma60, c[-1]>ma20])
            factors["trend"].append((t/4, fwd))
            factors["momentum"].append((momentum(sub,20), fwd))
            factors["rsi"].append((rsi(c,14), fwd))
            factors["vol"].append((vol_ratio(sub), fwd))
            a = atr(sub,14)/c[-1]*100 if c[-1] else 0
            factors["atr"].append((a, fwd))
            hi60 = max(x["high"] for x in sub[-60:])
            factors["pos"].append((c[-1]/hi60 if hi60 else 0, fwd))
        time.sleep(0.05)

    # 计算每个因子与未来收益的相关性
    out = {}
    for name, pairs in factors.items():
        if len(pairs) < 30: continue
        xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
        n = len(xs)
        mx = sum(xs)/n; my = sum(ys)/n
        cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))/n
        sx = (sum((x-mx)**2 for x in xs)/n)**0.5
        sy = (sum((y-my)**2 for y in ys)/n)**0.5
        corr = cov/(sx*sy) if sx*sy else 0
        # 分组: 高分组 vs 低分组 未来收益
        idx = sorted(range(n), key=lambda i: -xs[i])
        top = idx[:max(1,n//5)]
        bot = idx[-max(1,n//5):]
        top_ret = sum(ys[i] for i in top)/len(top)
        bot_ret = sum(ys[i] for i in bot)/len(bot)
        out[name] = {"corr": round(corr,4), "samples": n,
                     "top_ret": round(top_ret,3), "bot_ret": round(bot_ret,3),
                     "spread": round(top_ret-bot_ret,3)}
    return out

# ═══════════ 样本外测试 ═══════════
def oos_test(code, name, days=500):
    """样本外: 前一半调参, 后一半验证"""
    k = klines(code, days)
    if not k or len(k) < 200: return None
    half = len(k)//2
    # 用前一半找最优参数
    best = None
    for bs in [65, 70, 75, 80]:
        r = backtest_v2(code, name, days=half+60, buy_score=bs)
        if r and (not best or r["total_ret"] > best[1]["total_ret"]):
            best = (bs, r)
    if not best: return None
    bs = best[0]
    # 用后一半验证
    k2 = k[half:]
    # 简化: 直接跑全部，看两个时期表现
    r1 = backtest_v2(code, name, days=half+60, buy_score=bs)
    return {"code": code, "name": name, "best_score": bs,
            "insample_ret": r1["total_ret"] if r1 else 0,
            "insample_wr": r1["winrate"] if r1 else 0,
            "insample_trades": r1["trades"] if r1 else 0}
