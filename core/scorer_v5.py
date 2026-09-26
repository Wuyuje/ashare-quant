#!/usr/bin/env python3
"""A股选股评分 v5 — 加入量价新因子 + 优化卖出
新增因子（全部本地可算，不依赖外部接口）:
  ① 相对强度 RS — 个股 vs 指数
  ② 价格加速度 — 涨势是否加速
  ③ 成交量趋势 — 量能持续放大
  ④ 波动收缩 — 缩量整理后突破
  ⑤ 均线发散度 — 多头强度

优化卖出:
  · 分批止盈（+5%卖一半，+10%再卖）
  · 趋势持有（不破MA10不走）
  · 移动止损（盈利后上移）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import ma, rsi, atr, momentum, vol_ratio

def score_v5(k, bm=None, i=None):
    """v5 评分（含量价新因子）
    k: 个股K线
    bm: 指数K线（用于相对强度）
    i: 当前索引（默认 len(k)-1）
    返回 (总分, 明细) 或 None
    """
    if not k or len(k) < 65: return None
    if i is None: i = len(k) - 1
    kk = k[:i+1]
    c = [x["close"] for x in kk]
    d = {}
    sc = 0.0

    # ═══ ① RSI 区间 (20分) — 实测最优 50-60 ═══
    r = rsi(c, 14)
    d["rsi"] = round(r, 1)
    if 50 <= r <= 60:   rsc = 20
    elif 45 <= r < 50:  rsc = 15
    elif 60 < r <= 68:  rsc = 13
    elif 40 <= r < 45:  rsc = 8
    elif 68 < r <= 75:  rsc = 6
    else:               rsc = 2
    sc += rsc
    d["rsi_s"] = rsc

    # ═══ ② 量比 (15分) — >2.5 最优 ═══
    vr = vol_ratio(kk)
    d["volr"] = round(vr, 2)
    if vr >= 2.5:   vsc = 15
    elif vr >= 1.8: vsc = 13
    elif vr >= 1.3: vsc = 10
    elif vr >= 1.0: vsc = 6
    else:           vsc = 2
    sc += vsc
    d["vol_s"] = vsc

    # ═══ ③ 相对强度 RS (20分) — 新因子 ═══
    # 个股20日涨幅 - 指数20日涨幅
    rs = 0
    if i >= 20:
        stk_ret = (c[-1] - c[-21]) / c[-21] * 100
        idx_ret = 0
        if bm and i < len(bm) and i >= 20:
            bc = [x["close"] for x in bm[:i+1]]
            if bc[-21] > 0:
                idx_ret = (bc[-1] - bc[-21]) / bc[-21] * 100
        rs = stk_ret - idx_ret
    d["rs"] = round(rs, 2)
    if rs >= 15:    rss = 20
    elif rs >= 8:   rss = 17
    elif rs >= 3:   rss = 13
    elif rs >= 0:   rss = 9
    elif rs >= -5:  rss = 5
    else:           rss = 1
    sc += rss
    d["rs_s"] = rss

    # ═══ ④ 价格加速度 (15分) — 新因子 ═══
    accel = 0
    if i >= 20:
        r5 = (c[-1] - c[-6]) / c[-6] * 100 if c[-6] else 0
        r20 = (c[-1] - c[-21]) / c[-21] * 100 if c[-21] else 0
        accel = r5 - r20/4      # 近5日 vs 20日均速
    d["accel"] = round(accel, 2)
    if accel >= 3:      asc = 15
    elif accel >= 1:    asc = 12
    elif accel >= 0:    asc = 8
    elif accel >= -2:   asc = 4
    else:               asc = 1
    sc += asc
    d["accel_s"] = asc

    # ═══ ⑤ 成交量趋势 (10分) — 新因子 ═══
    vt = 1
    if i >= 20:
        v5 = sum(x["vol"] for x in kk[-5:])/5
        v20 = sum(x["vol"] for x in kk[-20:])/20
        vt = v5/v20 if v20 else 1
    d["vol_trend"] = round(vt, 2)
    if vt >= 1.5:   vts = 10
    elif vt >= 1.2: vts = 8
    elif vt >= 1.0: vts = 6
    elif vt >= 0.8: vts = 3
    else:           vts = 1
    sc += vts
    d["vt_s"] = vts

    # ═══ ⑥ 均线发散度 (10分) ═══
    ma5, ma10, ma20, ma60 = ma(c,5), ma(c,10), ma(c,20), ma(c,60)
    spread = (ma5 - ma20)/ma20*100 if ma5 and ma20 else 0
    d["spread"] = round(spread, 2)
    if 1 <= spread <= 8:    sps = 10
    elif 0 <= spread < 1:   sps = 7
    elif 8 < spread <= 15:  sps = 6
    else:                   sps = 2
    sc += sps
    d["sp_s"] = sps

    # ═══ ⑦ 位置 (10分) ═══
    hi60 = max(x["high"] for x in kk[-60:])
    pos = c[-1]/hi60 if hi60 else 0
    d["pos60"] = round(pos*100, 1)
    if pos >= 0.97:     psc = 10
    elif pos >= 0.90:   psc = 8
    elif pos >= 0.80:   psc = 6
    elif pos < 0.60:    psc = 5
    else:               psc = 3
    sc += psc
    d["pos_s"] = psc

    # ═══ 过滤（排除明显差的情况）═══
    dev20 = (c[-1] - ma20)/ma20*100 if ma20 else 0
    a = atr(kk, 14)/c[-1]*100 if c[-1] else 0
    if dev20 > 18: return None          # 偏离20线太远（追高风险）
    if a < 1.0: return None             # 波动太小（死水）
    if pos < 0.45 and rs < 0: return None  # 低位+弱势

    return round(sc, 1), d


# ═══════════ 优化卖出逻辑 ═══════════
def sell_signal(k, i, pos, cfg):
    """判断是否卖出
    pos: {cost, peak, qty, partial_done(已分批卖过)}
    cfg: 卖出参数
    返回 (reason, sell_ratio) 或 (None, 0)
    sell_ratio: 1.0=全卖, 0.5=卖一半
    """
    if i >= len(k): return None, 0
    price = k[i]["close"]
    # ⚠️ 用"纯买入价"算收益率（与v4一致），不用含手续费的cost
    base = pos.get("px") or pos.get("cost") or price
    ret = (price - base) / base * 100
    peak = max(pos.get("peak", base), price)
    peak_ret = (peak - base) / base * 100

    # ① 止损
    if ret <= cfg.get("stop_loss", -7):
        return f"止损({ret:.1f}%)", 1.0

    # ② 分批止盈
    if cfg.get("partial", True):
        if ret >= cfg.get("tp2", 10) and not pos.get("partial2"):
            return f"止盈二档({ret:.1f}%)", 0.5
        if ret >= cfg.get("tp1", 5) and not pos.get("partial1"):
            return f"止盈一档({ret:.1f}%)", 0.5

    # ③ 移动止盈（峰值回撤）
    trail = cfg.get("trail", 4)
    if peak_ret >= cfg.get("trail_start", 5) and (peak_ret - ret) >= trail:
        return f"移动止盈(峰{peak_ret:.1f}%→{ret:.1f}%)", 1.0

    # ④ 趋势持有：破MA10卖出（延长持有期）
    if cfg.get("trend_hold", True):
        c = [x["close"] for x in k[:i+1]]
        ma10 = ma(c, 10)
        if ma10 and price < ma10 and ret < 0:
            return f"破MA10({ret:.1f}%)", 1.0

    # ⑤ 最长持有（放宽到20天）
    held = i - (pos.get("open_i") if pos.get("open_i") is not None else pos.get("i", i))
    if held >= cfg.get("max_hold", 20):
        return f"到期({held}天)", 1.0

    return None, 0


if __name__ == "__main__":
    from adata import klines, index_klines
    bm = index_klines("sh000300", 300)
    for code in ["600519", "000001"]:
        k = klines(code, 150)
        if k:
            r = score_v5(k, bm, len(k)-1)
            if r:
                print(f"{code}: 评分{r[0]} {r[1]}")
            else:
                print(f"{code}: 被过滤")
