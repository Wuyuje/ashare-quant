#!/usr/bin/env python3
"""A股数据层 — 免费公开数据源（东方财富/腾讯/新浪）
独立模块，不与现有系统冲突
"""
import json, urllib.request, time, os, re, sys
sys.stdout.reconfigure(line_buffering=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "..", "data", "cache")
os.makedirs(CACHE, exist_ok=True)

def _g(url, hdr=None, to=15, retry=3):
    h = dict(UA)
    if hdr: h.update(hdr)
    for i in range(retry):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=to)
            return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retry - 1:
                return None
            time.sleep(0.6)

# ═══════════════ 1. 全市场股票列表 ═══════════════
def all_stocks():
    """东方财富: 全市场A股列表（含实时价、涨跌幅、成交额）"""
    out = []
    for pn in range(1, 60):        # 每页100，最多60页=6000只
        u = ("https://push2.eastmoney.com/api/qt/clist/get?"
             f"pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&"
             "fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&"
             "fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152")
        b = _g(u)
        if not b: break
        try:
            d = json.loads(b)
        except Exception:
            break
        data = d.get("data") or {}
        diff = data.get("diff")
        if not diff:
            break
        items = list(diff.values()) if isinstance(diff, dict) else diff
        if not items:
            break
        for x in items:
            code = x.get("f12") or ""
            if not code or not re.match(r"^\d{6}$", code): continue
            out.append({
                "code": code,
                "name": x.get("f14") or "",
                "price": x.get("f2"),
                "chg_pct": x.get("f3"),
                "chg": x.get("f4"),
                "vol": x.get("f5"),
                "amount": x.get("f6"),
                "amplitude": x.get("f7"),
                "turnover": x.get("f8"),
                "pe": x.get("f9"),
                "pb": x.get("f23"),
                "vol_ratio": x.get("f10"),
                "high": x.get("f15"),
                "low": x.get("f16"),
                "open": x.get("f17"),
                "prev_close": x.get("f18"),
                "market_cap": x.get("f20"),
                "float_cap": x.get("f21"),
                "mkt": x.get("f13"),   # 0=深 1=沪
            })
        if len(items) < 100:
            break
        time.sleep(0.15)
    return out

# ═══════════════ 2. K线数据 ═══════════════
def klines(code, days=250, period="day", fq=1):
    """东方财富K线
    period: day/week/month | 分钟: 1/5/15/30/60
    fq: 1=前复权 2=后复权 0=不复权
    """
    mkt = 1 if code.startswith(("6", "5", "9")) else 0
    secid = f"{mkt}.{code}"
    klt = {"1m":1,"5m":5,"15m":15,"30m":30,"60m":60,"day":101,"week":102,"month":103}.get(period, 101)
    u = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
         f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&"
         f"fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
         f"klt={klt}&fqt={fq}&end=20500101&lmt={days}")
    b = _g(u)
    if not b: return []
    try:
        d = json.loads(b)
    except Exception:
        return []
    kl = ((d.get("data") or {}).get("klines")) or []
    out = []
    for line in kl:
        p = line.split(",")
        if len(p) < 6: continue
        out.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]), "vol": float(p[5]),
            "amount": float(p[6]) if len(p) > 6 else 0,
            "amplitude": float(p[7]) if len(p) > 7 else 0,
            "chg_pct": float(p[8]) if len(p) > 8 else 0,
            "chg": float(p[9]) if len(p) > 9 else 0,
            "turnover": float(p[10]) if len(p) > 10 else 0,
        })
    return out

# ═══════════════ 3. 实时行情（腾讯，快） ═══════════════
def quote_tencent(codes):
    """批量实时行情，codes=['sh600519','sz000001'] 或 ['600519','000001']"""
    if not codes: return {}
    norm = []
    for c in codes:
        c = str(c).strip().lower()
        if c.startswith(("sh", "sz", "bj")):
            norm.append(c)
        else:
            norm.append(("sh" if c.startswith(("6","5","9")) else "sz") + c)
    u = "https://qt.gtimg.cn/q=" + ",".join(norm)
    b = _g(u)
    if not b: return {}
    out = {}
    for line in b.strip().split("\n"):
        if "=" not in line: continue
        var, val = line.split("=", 1)
        code = var.replace("v_", "").strip()
        p = val.strip().strip('";').split("~")
        if len(p) < 40: continue
        try:
            out[code] = {
                "name": p[1], "code": p[2], "price": float(p[3]),
                "prev_close": float(p[4]), "open": float(p[5]),
                "vol": float(p[6]) if p[6] else 0,
                "high": float(p[33]) if p[33] else 0,
                "low": float(p[34]) if p[34] else 0,
                "amount": float(p[37]) if len(p)>37 and p[37] else 0,
                "chg": float(p[31]) if p[31] else 0,
                "chg_pct": float(p[32]) if p[32] else 0,
                "turnover": float(p[38]) if len(p)>38 and p[38] else 0,
                "pe": float(p[39]) if len(p)>39 and p[39] else 0,
                "pb": float(p[46]) if len(p)>46 and p[46] else 0,
                "time": p[30] if len(p)>30 else "",
            }
        except Exception:
            continue
    return out

# ═══════════════ 4. 指数 ═══════════════
def index_quote():
    """主要指数: 上证/深证/创业板/沪深300"""
    codes = ["sh000001", "sz399001", "sz399006", "sh000300"]
    return quote_tencent(codes)

# ═══════════════ 5. 资金流（东财） ═══════════════
def money_flow(code):
    """个股资金流"""
    mkt = 1 if code.startswith(("6", "5", "9")) else 0
    u = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
         f"secid={mkt}.{code}&fields1=f1,f2,f3,f7&"
         "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&klt=101&lmt=5")
    b = _g(u)
    if not b: return []
    try:
        d = json.loads(b)
    except Exception:
        return []
    kl = ((d.get("data") or {}).get("klines")) or []
    out = []
    for line in kl:
        p = line.split(",")
        if len(p) < 6: continue
        out.append({"date": p[0], "main": float(p[1]) if p[1] else 0,
                    "super": float(p[2]) if len(p)>2 and p[2] else 0,
                    "big": float(p[3]) if len(p)>3 and p[3] else 0,
                    "mid": float(p[4]) if len(p)>4 and p[4] else 0,
                    "small": float(p[5]) if len(p)>5 and p[5] else 0})
    return out

if __name__ == "__main__":
    print("=== 测试 A股数据层 ===")
    q = quote_tencent(["600519", "000001", "300750"])
    for k, v in q.items():
        print(f"  {v['name']} ({v['code']}) {v['price']} {v['chg_pct']:+.2f}%")
    print()
    k = klines("600519", 5)
    print(f"  贵州茅台 日K 最近{len(k)}根: {k[-1] if k else '无'}")
    print()
    ix = index_quote()
    for k, v in ix.items():
        print(f"  {v['name']} {v['price']} {v['chg_pct']:+.2f}%")
