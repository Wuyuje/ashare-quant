#!/usr/bin/env python3
"""组合级回测 — 一次持N只，分散风险
解决单票风险大（-50%）的问题
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import klines, index_klines
from scorer_v4 import score_v4
from strategy_v3 import market_state

COST = {"commission":0.00025,"stamp":0.0005,"slippage":0.0015,"min_fee":5}

def buy_cost(px,q):
    g=px*q; return g+max(5,g*0.00025)+g*0.0015
def sell_net(px,q):
    g=px*q; return g-max(5,g*0.00025)-g*0.0005-g*0.0015

def portfolio_bt(data, bm, cfg):
    """组合回测
    data: {code: {'name':..., 'k':[...]}}
    bm: 沪深300 K线
    cfg: 参数
    """
    capital = cfg.get("capital", 100000)
    cash = capital
    max_pos = cfg.get("max_pos", 5)
    pos_pct = cfg.get("pos_pct", 0.18)
    hold = cfg.get("hold", 10)
    buy_sc = cfg.get("buy_score", 60)
    sl = cfg.get("stop_loss", -7)
    trail = cfg.get("trail", 3)
    use_market = cfg.get("use_market", True)

    positions = {}     # code -> {qty, cost, open_i, peak, cost_total}
    dates = []
    equity = []

    # 用所有股票的共同日期轴
    code0 = list(data.keys())[0]
    n = min(len(v["k"]) for v in data.values())
    for i in range(60, n-1):
        # 大盘择时
        can_buy = True
        if use_market and bm and i < len(bm):
            if market_state(bm, i) == "bear":
                can_buy = False

        # ① 处理持仓（卖出）
        to_close = []
        for code, p in positions.items():
            if i >= len(data[code]["k"]): continue
            price = data[code]["k"][i]["close"]
            prev = data[code]["k"][i-1]["close"] if i > 0 else price
            ret = (price - p["cost"]) / p["cost"] * 100
            held = i - p["open_i"]
            p["peak"] = max(p["peak"], price)
            pr = (p["peak"] - p["cost"]) / p["cost"] * 100
            reason = None
            if ret <= sl: reason = "止损"
            elif trail and pr >= 3 and (pr - ret) >= trail: reason = "移动止盈"
            elif held >= hold: reason = "到期"
            if reason:
                # 跌停不能卖
                if prev > 0 and (price-prev)/prev*100 <= -9.8: continue
                to_close.append((code, price, reason, ret))
        for code, price, reason, ret in to_close:
            p = positions[code]
            net = sell_net(price, p["qty"])
            cash += net
            del positions[code]

        # ② 找买入
        if can_buy and len(positions) < max_pos and cash > capital * 0.05:
            picks = []
            for code, v in data.items():
                if code in positions: continue
                if i >= len(v["k"]): continue
                r = score_v4(v["k"][:i+1])
                if not r: continue
                sc = r[0]
                if sc >= buy_sc:
                    price = v["k"][i]["close"]
                    prev = v["k"][i-1]["close"] if i > 0 else price
                    if prev > 0 and (price-prev)/prev*100 >= 9.8: continue
                    picks.append((sc, code, price))
            picks.sort(key=lambda x: -x[0])
            slots = max_pos - len(positions)
            for sc, code, price in picks[:slots]:
                budget = min(cash * pos_pct, cash - 1000)
                if budget < 3000: break
                qty = int(budget / price / 100) * 100
                if qty < 100: continue
                t = buy_cost(price, qty)
                if t > cash: continue
                cash -= t
                positions[code] = {"qty": qty, "cost": price, "open_i": i,
                                   "peak": price, "cost_total": t}

        # ③ 记录净值
        mv = 0
        for code, p in positions.items():
            if i < len(data[code]["k"]):
                mv += data[code]["k"][i]["close"] * p["qty"]
        equity.append(cash + mv)

    if not equity: return None
    peak = equity[0]; mdd = 0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, (v-peak)/peak*100)
    total_ret = (equity[-1] - capital) / capital * 100
    return {"final": round(equity[-1],2), "ret": round(total_ret,2),
            "mdd": round(mdd,2), "days": len(equity),
            "max_pos_used": max_pos}
