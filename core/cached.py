
import json,os,time
sys_path="/root/ashare-quant/data/stock_list.json"
import sys
sys.path.insert(0,"/root/ashare-quant/core")
from adata import all_stocks

def cached_stocks(max_age=3600):
    """带缓存的股票列表"""
    try:
        d=json.load(open(sys_path))
        if time.time()-d.get("t",0) < max_age and d.get("stocks"):
            return d["stocks"]
    except Exception: pass
    st=all_stocks()
    if st:
        json.dump({"t":int(time.time()),"stocks":st},open(sys_path,"w"),ensure_ascii=False)
    return st
