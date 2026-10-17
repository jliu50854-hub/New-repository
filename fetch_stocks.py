import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/"
}

def get_market_overview():
    """获取大盘主要指数点位及真实涨跌幅"""
    url = "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
    indices = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
        for line in resp.text.strip().split(";"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) > 5:
                cur_p = float(parts[3])
                pre_close = float(parts[4])
                chg_pct = ((cur_p - pre_close) / pre_close * 100) if pre_close > 0 else 0.0
                indices.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": f"{cur_p:.2f}",
                    "change": f"{chg_pct:+.2f}%",
                    "change_num": round(chg_pct, 2)
                })
    except Exception as e:
        print(f"获取大盘指数失败: {e}")
    return indices

def fetch_single_page(page):
    """抓取单页数据（每页100条）"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": page,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f14,f2,f3,f8,f10,f20,f9"
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            res_json = resp.json()
            return res_json.get("data", {}).get("diff", [])
    except Exception as e:
        print(f"拉取第 {page} 页失败: {e}")
    return []

def fetch_all_a_shares():
    """
    并发拉取 55 页（每页100只，覆盖全市场 5400+ 只 A 股）
    使用 8 线程并发，3秒左右即可全部取完
    """
    total_pages = 56
    all_records = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_single_page, page) for page in range(1, total_pages + 1)]
        for f in as_completed(futures):
            res = f.result()
            if res:
                all_records.extend(res)
                
    return all_records

def main():
    print("开始并发抓取全市场 A 股完整行情（5000+只）...")
    raw_list = fetch_all_a_shares()
    print(f"成功获取到 {len(raw_list)} 条原始行情记录，开始打标签与清洗...")

    indices = get_market_overview()

    stocks = []
    up_count = 0
    down_count = 0
    limit_up = 0
    seen_codes = set()

    for item in raw_list:
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))

        if not code or code in seen_codes:
            continue
        seen_codes.add(code)

        # 价格与涨跌幅清洗
        price_val = item.get("f2")
        chg_val = item.get("f3")
        if price_val == "-" or chg_val == "-" or price_val is None or chg_val is None:
            continue

        try:
            price = float(price_val)
            chg = float(chg_val)
            to = float(item.get("f8", 0)) if item.get("f8") != "-" and item.get("f8") is not None else 0.0
            vr = float(item.get("f10", 1.0)) if item.get("f10") != "-" and item.get("f10") is not None else 1.0
            mv_raw = item.get("f20")
            mv = (float(mv_raw) / 100000000.0) if mv_raw and mv_raw != "-" else 0.0
            pe_raw = item.get("f9")
            pe = float(pe_raw) if pe_raw and pe_raw != "-" else 0.0
        except (ValueError, TypeError):
            continue

        # 统计涨跌家数
        if chg > 0:
            up_count += 1
            if chg >= 9.8:
                limit_up += 1
        elif chg < 0:
            down_count += 1

        # 映射拼音代码供前端调用 K 线图
        if code.startswith("6"):
            symbol = f"sh{code}"
        elif code.startswith(("0", "3")):
            symbol = f"sz{code}"
        else:
            symbol = f"bj{code}"

        # 潜力股筛选策略
        tag = "全市场"
        reason = "普通流通股"
        is_st = "ST" in name or "退" in name

        if not is_st and price >= 4.0:
            if 3.0 <= chg <= 8.5 and 5.0 <= to <= 18.0 and vr >= 1.6:
                tag = "量价突破"
                reason = f"放量上攻（量比{vr:.1f}，换手{to:.1f}%），形态强势"
            elif 1.5 <= chg <= 4.0 and 3.0 <= to <= 9.0 and 1.3 <= vr <= 2.5:
                tag = "温和蓄势"
                reason = f"底部温和放量，换手{to:.1f}%，量比{vr:.1f}，回撤风险较低"
            elif to >= 10.0 and vr >= 1.4 and chg > 0:
                tag = "高换手博弈"
                reason = f"短线活跃资金博弈（换手{to:.1f}%），量比{vr:.1f}"

        mv_str = f"{mv:.1f}亿" if mv > 0 else "--"
        pe_str = f"{pe:.1f}" if pe > 0 else ("亏损" if pe < 0 else "--")

        stocks.append({
            "code": code,
            "symbol": symbol,
            "name": name,
            "price": f"{price:.2f}",
            "change": f"{chg:+.2f}%",
            "change_num": round(chg, 2),
            "turnover": f"{to:.2f}%",
            "turnover_num": to,
            "volume_ratio": f"{vr:.2f}",
            "volume_ratio_num": vr,
            "market_cap": mv_str,
            "market_cap_num": round(mv, 1),
            "pe": pe_str,
            "tag": tag,
            "reason": reason
        })

    # 默认按涨跌幅降序
    stocks.sort(key=lambda x: x["change_num"], reverse=True)

    tz_bj = timezone(timedelta(hours=8))
    now = datetime.now(tz_bj)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "date": date_str,
        "update_time": time_str,
        "total_count": len(stocks),
        "market_overview": {
            "indices": indices,
            "up_count": up_count,
            "down_count": down_count,
            "limit_up": limit_up
        },
        "stocks": stocks
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    os.makedirs("history", exist_ok=True)
    with open(f"history/{date_str}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"数据处理完毕！全市场共收录 {len(stocks)} 只股票，已写入 data.json。")

if __name__ == "__main__":
    main()
