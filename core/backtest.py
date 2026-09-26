#!/usr/bin/env python3
"""A股回测引擎 — 验证选股策略历史表现
独立模块
"""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adata import klines, all_stocks
from screener import score_stock, ma, rsi, atr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

def backtest_one(code, name, days=250, hold=5, top_score=70, stop_loss=-5, take_profit=10):
    """单只股票回测
    hold: 持有天数
    top_score: 买入评分门槛
    """
    k = klines(code, days)
    if not k or len(k) < 80:
        return None
    trades = []
    i = 60
    pos = None
    while i < len(k) - hold:
        sub = k[:i+1]
        r = score_stock(sub)
        if not r:
            i += 1; continue
        sc, detail = r

        if pos is None:
            # 买入条件: 评分达标
            if sc >= top_score:
                pos = {"idx": i, "price": k[i]["close"], "date": k[i]["date"], "score": sc}
        else:
            # 持仓: 检查卖出
            cur = k[i]["close"]
            ret = (cur - pos["price"])/pos["price"]*100
            held = i - pos["idx"]
            reason = None
            if ret <= stop_loss: reason = "止损"
            elif ret >= take_profit: reason = "止盈"
            elif held >= hold: reason = "到期"
            if reason:
                trades.append({**pos, "sell_idx": i, "sell_price": cur,
                               "sell_date": k[i]["date"], "ret": round(ret,2),
                               "held": held, "reason": reason})
                pos = None
        i += 1

    if not trades:
        return None
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    tot = sum(t["ret"] for t in trades)
    # 最大回撤
    eq = 0; peak = 0; mdd = 0
    for t in trades:
        eq += t["ret"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "code": code, "name": name, "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "winrate": round(len(wins)/len(trades)*100, 1) if trades else 0,
        "total_ret": round(tot, 2),
        "avg_ret": round(tot/len(trades), 2) if trades else 0,
        "avg_win": round(sum(t["ret"] for t in wins)/len(wins),2) if wins else 0,
        "avg_loss": round(sum(t["ret"] for t in losses)/len(losses),2) if losses else 0,
        "max_dd": round(mdd, 2),
        "detail": trades,
    }

def backtest_batch(codes=None, limit=30, **kw):
    """批量回测"""
    if not codes:
        print("📊 拉取股票列表...", flush=True)
        stocks = all_stocks()
        # 选成交额大的
        stocks = [s for s in stocks if s.get("amount") and float(s["amount"]) > 5e8]
        stocks.sort(key=lambda x: -float(x["amount"]))
        codes = [(s["code"], s["name"]) for s in stocks[:limit]]
    else:
        codes = [(c, c) for c in codes]

    results = []
    for i, (code, name) in enumerate(codes):
        try:
            r = backtest_one(code, name, **kw)
            if r: results.append(r)
        except Exception as e:
            pass
        if (i+1) % 5 == 0:
            print(f"  已回测 {i+1}/{len(codes)}", flush=True)
        time.sleep(0.1)

    if not results: return []
    results.sort(key=lambda x: -x["total_ret"])
    return results

def report(results):
    if not results: 
        print("无结果"); return
    n = len(results)
    tot_trades = sum(r["trades"] for r in results)
    tot_wins = sum(r["wins"] for r in results)
    avg_ret = sum(r["total_ret"] for r in results)/n
    avg_wr = sum(r["winrate"] for r in results)/n
    print("="*80)
    print("📊 A股选股策略回测报告")
    print("="*80)
    print(f"  回测股票: {n} 只")
    print(f"  总交易: {tot_trades} 笔")
    print(f"  总胜率: {tot_wins/tot_trades*100:.1f}%" if tot_trades else "  总胜率: —")
    print(f"  平均收益: {avg_ret:+.2f}%")
    print(f"  平均胜率: {avg_wr:.1f}%")
    print()
    print(f"  {'代码':8s} {'名称':10s} {'交易':>5s} {'胜率':>7s} {'总收益':>9s} {'均收益':>8s} {'回撤':>8s}")
    print("  " + "-"*70)
    for r in results[:20]:
        print(f"  {r['code']:8s} {r['name']:10s} {r['trades']:>5d} {r['winrate']:>6.1f}% "
              f"{r['total_ret']:>8.2f}% {r['avg_ret']:>7.2f}% {r['max_dd']:>7.2f}%")

if __name__ == "__main__":
    print("=== A股策略回测 ===\n")
    res = backtest_batch(limit=15, days=250, hold=5, top_score=70)
    report(res)
    if res:
        os.makedirs(DATA, exist_ok=True)
        json.dump(res, open(os.path.join(DATA, "backtest_result.json"), "w"),
                  ensure_ascii=False, indent=1)
        print(f"\n  结果已存: data/backtest_result.json")
