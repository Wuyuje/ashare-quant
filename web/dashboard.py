#!/usr/bin/env python3
"""A股量化系统 — Web 仪表盘
独立服务，端口 10600
"""
import http.server, socketserver, json, os, sys, time, datetime, threading
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, CORE)
PORT = int(os.getenv("PORT", "10600"))

def rd(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d

def get_state():
    return rd(os.path.join(DATA, "portfolio.json"), {})

def get_backtest():
    return rd(os.path.join(DATA, "backtest_result.json"), [])

def _live_quotes(codes):
    try:
        from adata import quote_tencent
        return quote_tencent(codes)
    except Exception:
        return {}

HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股量化系统</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#e6edf8;font-family:-apple-system,"PingFang SC",system-ui,sans-serif;padding:14px;line-height:1.6}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:20px;font-weight:800;margin-bottom:3px}
.sub{color:#7d90ad;font-size:11.5px;margin-bottom:14px}
.tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.tab{padding:7px 15px;border-radius:8px;background:rgba(255,255,255,.05);border:1px solid #1f2a3d;
  color:#8fa3c0;font-size:12.5px;font-weight:600;cursor:pointer;transition:.15s}
.tab:hover{border-color:#3b82f6;color:#93c5fd}
.tab.on{background:rgba(59,130,246,.2);border-color:#3b82f6;color:#93c5fd}
.panel{display:none}.panel.on{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px;margin-bottom:14px}
.card{background:#111827;border:1px solid #1f2a3d;border-radius:11px;padding:13px}
.card .lb{font-size:10.5px;color:#7d90ad;margin-bottom:4px}
.card .vl{font-size:21px;font-weight:800}
.g{color:#34d399}.r{color:#f87171}.y{color:#fbbf24}
.sec{background:#111827;border:1px solid #1f2a3d;border-radius:11px;padding:14px;margin-bottom:12px}
.sec h2{font-size:13.5px;font-weight:700;margin-bottom:10px;color:#93c5fd}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 7px;color:#7d90ad;font-weight:600;font-size:10.5px;border-bottom:1px solid #1f2a3d}
td{padding:6px 7px;border-bottom:1px solid rgba(255,255,255,.04)}
tr:last-child td{border-bottom:none}
.mut{color:#5c7092}
.btn{padding:7px 15px;border-radius:8px;background:#3b82f6;color:#fff;border:none;
  font-size:12.5px;font-weight:700;cursor:pointer;transition:.15s}
.btn:hover{background:#2563eb}
.btn.gray{background:rgba(255,255,255,.07);color:#c8d6ea;border:1px solid #1f2a3d}
</style></head><body>
<div class="wrap">
<h1>📈 A股量化系统</h1>
<div class="sub" id="sub">加载中...</div>

<div class="tabs">
  <div class="tab on" onclick="sw('overview',this)">📊 总览</div>
  <div class="tab" onclick="sw('positions',this)">💼 持仓</div>
  <div class="tab" onclick="sw('trades',this)">🧾 交易记录</div>
  <div class="tab" onclick="sw('backtest',this)">🔬 回测</div>
</div>

<div class="panel on" id="p-overview">
  <div class="cards" id="ovCards"></div>
  <div class="sec"><h2>💰 资金曲线（模拟）</h2><div id="ovChart" style="height:110px"></div></div>
</div>

<div class="panel" id="p-positions">
  <div class="sec"><h2>💼 当前持仓</h2><div id="posBox"><span class="mut">加载中...</span></div></div>
</div>

<div class="panel" id="p-trades">
  <div class="sec"><h2>🧾 交易记录</h2><div id="trBox"><span class="mut">加载中...</span></div></div>
</div>

<div class="panel" id="p-backtest">
  <div class="sec"><h2>🔬 策略回测结果（历史验证）</h2><div id="btBox"><span class="mut">加载中...</span></div></div>
</div>

<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
  <button class="btn" onclick="runScan()">🔄 立即扫描选股</button>
  <button class="btn gray" onclick="loadAll()">刷新</button>
</div>
</div>

<script>
function fp(v){return (v>0?'+':'')+Number(v).toFixed(2)}
function sw(id,el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  el.classList.add('on');
  document.getElementById('p-'+id).classList.add('on');
}
async function loadAll(){
  await loadOverview(); await loadPositions(); await loadTrades(); await loadBacktest();
}
async function loadOverview(){
  try{
    const d=await (await fetch('/api/stats')).json();
    document.getElementById('sub').textContent='更新 '+d.now+' · '+(d.trading_time?'交易时段':'非交易时段');
    const p=d.profit||0;
    document.getElementById('ovCards').innerHTML=
      '<div class="card"><div class="lb">总资产</div><div class="vl">'+(d.total||0).toFixed(0)+'</div></div>'+
      '<div class="card"><div class="lb">现金</div><div class="vl">'+(d.cash||0).toFixed(0)+'</div></div>'+
      '<div class="card"><div class="lb">持仓市值</div><div class="vl">'+(d.market_value||0).toFixed(0)+'</div></div>'+
      '<div class="card"><div class="lb">盈亏</div><div class="vl '+(p>=0?'g':'r')+'">'+fp(p)+'</div></div>'+
      '<div class="card"><div class="lb">收益率</div><div class="vl '+(p>=0?'g':'r')+'">'+(d.profit_pct||0).toFixed(2)+'%</div></div>'+
      '<div class="card"><div class="lb">持仓/胜率</div><div class="vl">'+(d.positions||0)+' / '+(d.winrate||0)+'%</div></div>';
    // 资金曲线
    const eq=d.equity||[];
    if(eq.length>1){
      const vs=eq.map(x=>x.v),mn=Math.min(...vs),mx=Math.max(...vs),rg=mx-mn||1;
      const pts=vs.map((v,i)=>((i/(vs.length-1))*100)+','+(100-((v-mn)/rg)*100)).join(' ');
      document.getElementById('ovChart').innerHTML=
        '<svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:100%">'+
        '<polyline points="'+pts+'" fill="none" stroke="#3b82f6" stroke-width="1.3" vector-effect="non-scaling-stroke"/></svg>';
    } else {
      document.getElementById('ovChart').innerHTML='<div class="mut" style="font-size:12px;padding:20px 0;text-align:center">暂无足够数据</div>';
    }
  }catch(e){}
}
async function loadPositions(){
  try{
    const d=await (await fetch('/api/positions')).json();
    const ps=d.positions||[];
    if(!ps.length){document.getElementById('posBox').innerHTML='<span class="mut">暂无持仓</span>';return;}
    let h='<table><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th><th>收益率</th><th>评分</th></tr>';
    ps.forEach(p=>{
      const cls=(p.ret||0)>=0?'g':'r';
      h+='<tr><td>'+p.code+'</td><td>'+p.name+'</td><td>'+p.qty+'</td>'+
        '<td>'+p.cost.toFixed(2)+'</td><td>'+(p.price||0).toFixed(2)+'</td>'+
        '<td class="'+cls+'">'+fp(p.pnl||0)+'</td>'+
        '<td class="'+cls+'">'+(p.ret||0).toFixed(2)+'%</td>'+
        '<td>'+(p.score||0).toFixed(0)+'</td></tr>';
    });
    document.getElementById('posBox').innerHTML=h+'</table>';
  }catch(e){}
}
async function loadTrades(){
  try{
    const d=await (await fetch('/api/trades')).json();
    const ts=d.trades||[];
    if(!ts.length){document.getElementById('trBox').innerHTML='<span class="mut">暂无交易</span>';return;}
    let h='<table><tr><th>时间</th><th>方向</th><th>代码</th><th>名称</th><th>价格</th><th>数量</th><th>金额</th><th>收益率</th><th>原因</th></tr>';
    ts.forEach(t=>{
      const isBuy=t.side==='BUY';
      const ret=t.ret!=null?((t.ret>=0?'+':'')+t.ret.toFixed(2)+'%'):'—';
      const cls=t.ret!=null?(t.ret>=0?'g':'r'):'';
      h+='<tr><td>'+t.time+'</td>'+
        '<td class="'+(isBuy?'r':'g')+'">'+(isBuy?'买入':'卖出')+'</td>'+
        '<td>'+t.code+'</td><td>'+t.name+'</td>'+
        '<td>'+(t.price||0).toFixed(2)+'</td><td>'+(t.qty||0)+'</td>'+
        '<td>'+((t.amount||0)/10000).toFixed(2)+'万</td>'+
        '<td class="'+cls+'">'+ret+'</td>'+
        '<td class="mut">'+(t.reason||'')+'</td></tr>';
    });
    document.getElementById('trBox').innerHTML=h+'</table>';
  }catch(e){}
}
async function loadBacktest(){
  try{
    const d=await (await fetch('/api/backtest')).json();
    const rs=d.results||[];
    if(!rs.length){document.getElementById('btBox').innerHTML='<span class="mut">暂无回测数据</span>';return;}
    let h='<table><tr><th>代码</th><th>名称</th><th>交易</th><th>胜率</th><th>总收益</th><th>均收益</th><th>最大回撤</th></tr>';
    rs.forEach(r=>{
      h+='<tr><td>'+r.code+'</td><td>'+r.name+'</td><td>'+r.trades+'</td>'+
        '<td class="'+(r.winrate>=50?'g':'r')+'">'+r.winrate.toFixed(1)+'%</td>'+
        '<td class="'+(r.total_ret>=0?'g':'r')+'">'+fp(r.total_ret)+'%</td>'+
        '<td>'+fp(r.avg_ret)+'%</td>'+
        '<td class="r">'+r.max_dd.toFixed(1)+'%</td></tr>';
    });
    document.getElementById('btBox').innerHTML=h+'</table>';
  }catch(e){}
}
async function runScan(){
  if(!confirm('立即执行一次扫描选股？（约需1-3分钟）'))return;
  const b=event.target; b.textContent='扫描中...'; b.disabled=true;
  try{
    const r=await (await fetch('/api/scan',{method:'POST'})).json();
    alert('扫描完成\\n持仓: '+(r.positions||0)+' 只\\n交易记录: '+(r.trades||0)+' 笔');
    loadAll();
  }catch(e){alert('扫描失败: '+e.message);}
  b.textContent='🔄 立即扫描选股'; b.disabled=false;
}
loadAll(); setInterval(loadAll,30000);
</script></body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def _h(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            return self._h(HTML)
        if p == "/api/stats":
            sys.path.insert(0, CORE)
            try:
                from executor import stats, is_trading_time, CFG
                st = stats()
                # 资金曲线（由交易记录推算）
                s = get_state()
                cash = CFG["init_cash"]; eq = []
                for t in (s.get("trades") or []):
                    if t["side"] == "SELL":
                        cash += (t.get("amount") or 0) - (t.get("fee") or 0)
                    else:
                        cash -= (t.get("amount") or 0) + (t.get("fee") or 0)
                    eq.append({"t": t.get("time"), "v": round(cash, 2)})
                st["equity"] = eq
                st["now"] = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
                st["trading_time"] = is_trading_time()
                return self._j(200, st)
            except Exception as e:
                return self._j(500, {"err": repr(e)[:100]})
        if p == "/api/positions":
            s = get_state()
            pos = s.get("positions") or {}
            out = []
            if pos:
                try:
                    from adata import quote_tencent
                    q = quote_tencent(list(pos.keys()))
                    for code, v in pos.items():
                        cur = v["cost"]
                        for k, qq in q.items():
                            if qq.get("code") == code:
                                cur = qq["price"]; break
                        ret = (cur - v["cost"]) / v["cost"] * 100 if v["cost"] else 0
                        out.append({"code": code, "name": v.get("name"), "qty": v["qty"],
                                    "cost": v["cost"], "price": cur,
                                    "pnl": round((cur - v["cost"]) * v["qty"], 2),
                                    "ret": round(ret, 2), "score": v.get("score")})
                except Exception:
                    pass
            return self._j(200, {"positions": out})
        if p == "/api/trades":
            s = get_state()
            ts = list(reversed(s.get("trades") or []))[:50]
            return self._j(200, {"trades": ts})
        if p == "/api/backtest":
            return self._j(200, {"results": get_backtest()})
        return self._h(HTML)

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/scan":
            def _run():
                try:
                    sys.path.insert(0, CORE)
                    from executor import scan_and_trade
                    scan_and_trade()
                except Exception:
                    pass
            threading.Thread(target=_run, daemon=True).start()
            time.sleep(2)
            s = get_state()
            return self._j(200, {"ok": True,
                                 "positions": len(s.get("positions") or {}),
                                 "trades": len(s.get("trades") or [])})
        return self._j(404, {"err": "nf"})

class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    print(f"📈 A股量化仪表盘: http://localhost:{PORT}", flush=True)
    with S(("", PORT), H) as srv:
        srv.serve_forever()
