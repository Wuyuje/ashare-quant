#!/usr/bin/env python3
"""A股策略 v2 — 大盘择时 + 趋势跟随 + 资金流
新增:
  ① 大盘择时: 沪深300 趋势判断（弱市空仓）
  ② 板块/趋势: 个股趋势强度
  ③ 资金流: 主力净流入
  ④ 移动止盈（已验证有效）
"""
import json, os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import all_stocks, klines, index_klines, quote_tencent
from screener import ma, rsi, atr, momentum, vol_ratio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)

COST = {"commission": 0.00025, "stamp": 0.0005, "slippage": 0.0015, "min_fee": 5}

def buy_cost(price, qty):
    g = price*qty
    return g + max(COST["min_fee"], g*COST["commission"]) + g*COST["slippage"]
def sell_net(price, qty):
    g = price*qty
    return g - max(COST["min_fee"], g*COST["commission"]) - g*COST["stamp"] - g*COST["slippage"]

# ═══════════ 大盘择时 ═══════════
def market_state(bm_k, i):
    """判断大盘状态
    返回: 'bull'(可开仓) | 'bear'(空仓) | 'neutral'
    """
    if not bm_k or i >= len(bm_k) or i < 60:
        return "neutral"
    c = [x["close"] for x in bm_k[:i+1]]
    ma20 = sum(c[-20:])/20
    ma60 = sum(c[-60:])/60
    cur = c[-1]
    # 趋势: 价格在20日线上 + 20日线在60日线上
    if cur > ma20 and ma20 > ma60:
        return "bull"
    # 价格跌破60日线
    if cur < ma60:
        return "bear"
    return "neutral"

def trend_strength(k, i):
    """个股趋势强度 0-100"""
    if i < 60: return 0
    c = [x["close"] for x in k[:i+1]]
    ma5 = sum(c[-5:])/5; ma10 = sum(c[-10:])/10
    ma20 = sum(c[-20:])/20; ma60 = sum(c[-60:])/60
    t = 0
    if ma5 > ma10: t += 25
    if ma10 > ma20: t += 25
    if ma20 > ma60: t += 25
    if c[-1] > ma20: t += 25
    return t

def money_flow_score(k, i):
    """资金流代理: 用成交额变化 + 涨跌"""
    if i < 20: return 50
    # 近期上涨日的成交额 vs 下跌日
    up_vol = 0; dn_vol = 0
    for j in range(i-19, i+1):
        if j <= 0: continue
        chg = k[j]["close"] - k[j-1]["close"]
        v = k[j]["vol"]
        if chg > 0: up_vol += v
        elif chg < 0: dn_vol += v
    if up_vol + dn_vol == 0: return 50
    r = up_vol / (up_vol + dn_vol)
    return min(100, r * 130)     # 0.77 → 100分

# ═══════════ 综合评分 v3 ═══════════
def score_v3(k, bm_k, i, cfg):
    """带大盘择时 + 趋势 + 资金流
    cfg:
      use_market: True/False
      market_filter: 'bull_only' | 'no_bear'
      w_trend, w_mom, w_rsi, w_vol, w_flow
    """
    if not k or i < 60: return None
    # ═══ 大盘择时 ═══
    ms = market_state(bm_k, i) if cfg.get("use_market", True) else "neutral"
    if cfg.get("market_filter", "no_bear") == "bull_only" and ms != "bull":
        return None                      # 只在大盘多头时交易
    if ms == "bear":
        return None                      # 熊市不开仓

    c = [x["close"] for x in k[:i+1]]
    sc = 0.0
    d = {"market": ms}

    # 趋势
    ts = trend_strength(k, i)
    sc += ts/100 * cfg.get("w_trend", 25)
    d["trend"] = ts

    # 动量
    mom = 0
    if i >= 20:
        mom = (c[-1]-c[-21])/c[-21]*100
    d["mom20"] = round(mom,2)
    if cfg.get("buy_mode") == "pullback":
        m = 20 if 0 <= mom <= 10 else (10 if mom <= 18 else (5 if mom < 0 else 0))
    else:
        m = max(0, min(20, (mom+8)/28*20))
    sc += m

    # RSI
    r = rsi(c[-15:], 14) if len(c) >= 15 else 50
    d["rsi"] = round(r,1)
    if cfg.get("buy_mode") == "pullback":
        rs = 15 if 40<=r<=62 else (10 if 35<=r<68 else 2)
    else:
        rs = 15 if 50<=r<=72 else (10 if 45<=r<50 else 3)
    sc += rs

    # 量能
    vr = 1.0
    if i >= 20:
        vn = sum(x["vol"] for x in k[i-4:i+1])/5
        vm = sum(x["vol"] for x in k[i-19:i+1])/20
        vr = vn/vm if vm else 1
    d["volr"] = round(vr,2)
    vs = 10 if vr>=1.3 else (7 if vr>=1.0 else 3)
    sc += vs

    # 资金流
    fs = money_flow_score(k, i)
    d["flow"] = round(fs,1)
    sc += fs/100 * cfg.get("w_flow", 15)

    # 位置（距60日高）
    hi60 = max(x["high"] for x in k[max(0,i-59):i+1])
    pos = c[-1]/hi60 if hi60 else 0
    d["pos60"] = round(pos*100,1)
    if cfg.get("buy_mode") == "pullback":
        ps = 15 if 0.72<=pos<=0.94 else (8 if pos>0.94 else 4)
    else:
        ps = 15 if pos>=0.95 else (12 if pos>=0.9 else (8 if pos>=0.8 else 3))
    sc += ps

    return (round(sc,1), d, ms) if not isinstance(sc, tuple) else (round(sc,1), d, ms)

# ═══════════ 回测 v3 ═══════════
def bt_v3(code, name, cfg, days=250):
    k = klines(code, days)
    if not k or len(k) < 80: return None
    # 大盘
    bm = None
    if cfg.get("use_market", True):
        bm = index_klines("sh000300", days)
        if not bm or len(bm) < 80:
            bm = index_klines("sh000001", days)

    cash = 100000.0; pos = None; trades = []; eq = []
    i = 60; hold = cfg.get("hold",5); bs = cfg.get("buy_score",75)
    sl = cfg.get("stop_loss",-7); tp = cfg.get("take_profit", None)
    trail = cfg.get("trail", 3)
    while i < len(k)-1:
        r = score_v3(k, bm, i, cfg)
        sc = r[0] if r else 0
        price = k[i]["close"]
        if pos is None:
            if sc >= bs:
                # 涨跌停检查
                if i>0 and k[i-1]["close"]>0:
                    chg=(price-k[i-1]["close"])/k[i-1]["close"]*100
                    if chg >= 9.8: i+=1; continue
                qty = int(cash*0.95/price/100)*100
                if qty >= 100:
                    t = buy_cost(price, qty)
                    if t <= cash:
                        cash -= t
                        pos = {"i":i,"px":price,"qty":qty,"dt":k[i]["date"],
                               "sc":sc,"cost":t,"peak":price}
        else:
            ret = (price-pos["px"])/pos["px"]*100
            held = i-pos["i"]
            pos["peak"] = max(pos["peak"], price)
            pr = (pos["peak"]-pos["px"])/pos["px"]*100
            reason = None
            if ret <= sl: reason = "止损"
            elif tp and ret >= tp: reason = "止盈"
            elif trail and pr >= 3 and (pr-ret) >= trail: reason = f"移动止盈"
            elif held >= hold: reason = "到期"
            elif sc < cfg.get("sell_score",45): reason = "评分降"
            if reason:
                if i>0 and k[i-1]["close"]>0:
                    chg=(price-k[i-1]["close"])/k[i-1]["close"]*100
                    if chg <= -9.8: i+=1; continue    # 跌停卖不出
                net = sell_net(price, pos["qty"])
                cash += net
                rr = (net-pos["cost"])/pos["cost"]*100
                trades.append({"ret":round(rr,2),"held":held,"reason":reason})
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
        peak = max(peak,v); mdd = min(mdd,(v-peak)/peak*100)
    return {"code":code,"name":name,"n":len(trades),"wins":len(wins),
            "wr":round(len(wins)/len(trades)*100,1),"ret":round(tot,2),
            "pool":round((eq[-1]-100000)/100000*100,2) if eq else 0,
            "mdd":round(mdd,2),
            "avg_win":round(sum(t["ret"] for t in wins)/len(wins),2) if wins else 0,
            "avg_loss":round(sum(t["ret"] for t in trades if t["ret"]<=0)/max(1,len(trades)-len(wins)),2)}

def batch(sample, cfg, label=""):
    rs = []
    for s in sample:
        try:
            x = bt_v3(s["code"], s["name"], cfg)
            if x: rs.append(x)
        except Exception: pass
        time.sleep(0.03)
    if not rs: return None
    n = len(rs); tt = sum(x["n"] for x in rs); tw = sum(x["wins"] for x in rs)
    ap = sum(x["pool"] for x in rs)/n
    win = len([x for x in rs if x["pool"]>0])
    mdd = sum(x["mdd"] for x in rs)/n
    aw = sum(x["avg_win"] for x in rs)/n
    al = sum(x["avg_loss"] for x in rs)/n
    return {"label":label,"stocks":n,"trades":tt,
            "wr":round(tw/tt*100,1) if tt else 0,"pool":round(ap,2),
            "win_stocks":win,"win_pct":round(win/n*100,1),
            "mdd":round(mdd,1),"avg_win":round(aw,2),"avg_loss":round(al,2)}
