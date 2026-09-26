#!/usr/bin/env python3
"""A股选股评分 v4 — 数据驱动（基于11600样本挖掘）
关键发现:
  RSI 50-60 最佳 (涨率54.9%)
  量比>2.5 最佳 (涨率63.6%)
  偏离20日线 0-5% 最佳 (涨率55.2%)
  ATR 4-6% 最佳 (涨率53.1%)
  60日位置>97% 好 (涨率52.4%)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import ma, rsi, atr, momentum, vol_ratio

# ═══ 基于数据的因子权重 ═══
# 每个因子按"实测超额收益"分配权重
WEIGHTS = {
    "rsi_zone":   25,   # RSI 50-60     超额+0.91
    "vol_surge":  20,   # 量比>2.5      超额+1.42
    "ma20_dev":   18,   # 偏离20线0-5%  超额+0.56
    "atr_zone":   15,   # ATR 4-6%      超额+0.59
    "pos60":      12,   # 60日位置>97%  超额+0.54
    "trend":      10,   # 均线多头
}

def score_v4(k):
    """返回 (总分, 明细) 总分0-100"""
    if not k or len(k) < 65: return None
    c = [x["close"] for x in k]
    d = {}; sc = 0.0

    # ═══ ① RSI 区间 (25分) — 实测最优 50-60 ═══
    r = rsi(c, 14)
    d["rsi"] = round(r, 1)
    if 50 <= r <= 60:   rsc = 25
    elif 45 <= r < 50:  rsc = 18
    elif 60 < r <= 68:  rsc = 16
    elif 40 <= r < 45:  rsc = 10
    elif 68 < r <= 75:  rsc = 8
    else:               rsc = 2
    sc += rsc
    d["rsi_score"] = rsc

    # ═══ ② 量比 (20分) — 实测 >2.5 最佳 ═══
    vr = vol_ratio(k)
    d["volr"] = round(vr, 2)
    if vr >= 2.5:   vsc = 20
    elif vr >= 1.8: vsc = 17
    elif vr >= 1.3: vsc = 13
    elif vr >= 1.0: vsc = 8
    else:           vsc = 3
    sc += vsc
    d["vol_score"] = vsc

    # ═══ ③ 偏离20日线 (18分) — 实测 0-5% 最佳 ═══
    ma20 = ma(c, 20)
    if ma20:
        dev = (c[-1] - ma20) / ma20 * 100
    else:
        dev = 0
    d["ma20_dev"] = round(dev, 2)
    if 0 <= dev <= 5:     dsc = 18
    elif -3 <= dev < 0:   dsc = 13
    elif 5 < dev <= 9:    dsc = 11
    elif -7 <= dev < -3:  dsc = 7
    elif 9 < dev <= 14:   dsc = 5
    else:                 dsc = 2
    sc += dsc
    d["dev_score"] = dsc

    # ═══ ④ 波动 ATR (15分) — 实测 4-6% 最佳 ═══
    a = atr(k, 14) / c[-1] * 100 if c[-1] else 0
    d["atr"] = round(a, 2)
    if 4 <= a <= 6:     asc = 15
    elif 2.5 <= a < 4:  asc = 11
    elif 6 < a <= 8:    asc = 9
    elif 1.5 <= a < 2.5: asc = 6
    else:               asc = 3
    sc += asc
    d["atr_score"] = asc

    # ═══ ⑤ 60日位置 (12分) — 实测 >97% 好 ═══
    hi60 = max(x["high"] for x in k[-60:])
    pos = c[-1] / hi60 if hi60 else 0
    d["pos60"] = round(pos * 100, 1)
    if pos >= 0.97:     psc = 12
    elif pos >= 0.90:   psc = 10
    elif pos < 0.60:    psc = 8      # 低位也有价值
    elif pos >= 0.80:   psc = 6
    else:               psc = 4
    sc += psc
    d["pos_score"] = psc

    # ═══ ⑥ 趋势 (10分) ═══
    ma5, ma10, ma60 = ma(c, 5), ma(c, 10), ma(c, 60)
    t = 0
    if ma5 and ma10 and ma20 and ma60:
        t = sum([ma5 > ma10, ma10 > ma20, ma20 > ma60, c[-1] > ma20])
    sc += t / 4 * 10
    d["trend"] = t

    # ═══ 过滤: 排除明显差的情况 ═══
    # 偏离20线太远(>15%) → 追高风险
    if dev > 15: return None
    # ATR太低(<1.2%) → 死水
    if a < 1.2: return None
    # 位置太低(<50%) 且 RSI<45 → 弱势
    if pos < 0.5 and r < 45: return None

    return round(sc, 1), d

if __name__ == "__main__":
    from adata import klines
    for code in ["600519", "000001"]:
        k = klines(code, 120)
        if k:
            r = score_v4(k)
            if r:
                print(f"{code}: 评分{r[0]} {r[1]}")
