import json
import os
import requests
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
    """获取大盘主要指数点位"""
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
                indices.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": f"{float(parts[3]):.2f}",
                    "change": f"{float(parts[5]):+.2f}%",
                    "change_num": float(parts[5])
                })
    except Exception as e:
        print(f"获取大盘指数失败: {e}")
    return indices

def fetch_all_a_shares():
    """
    单次批量拉取全市场 5300+ 只 A 股核心指标
    包含：代码、名称、最新价、涨跌幅、换手率、量比、总市值、市盈率等
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": 6000,  # 一次性获取全量数据，无需循环分页
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3", # 按涨跌幅降序
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048", # 覆盖沪深A股及北交所
        "fields": "f12,f14,f2,f3,f8,f10,f20,f9" 
        # f12:代码, f14:名称, f2:现价, f3:涨幅, f8:换手率, f10:量比, f20:总市值, f9:动态市盈率
    }

    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    data = resp.json()
    raw_list = data.get("data", {}).get("diff", [])
    return raw_list

def main():
    print("开始拉取全市场 A 股完整行情（5000+只）...")
    raw_list = fetch_all_a_shares()
    print(f"成功获取到 {len(raw_list)} 只股票数据，开始打标签与清洗...")

    indices = get_market_overview()

    stocks = []
    up_count = 0
    down_count = 0
    limit_up = 0

    for item in raw_list:
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))

        # 价格与涨跌幅清洗
        price_val = item.get("f2")
        chg_val = item.get("f3")
        if price_val == "-" or chg_val == "-" or price_val is None or chg_val is None:
            continue

        price = float(price_val)
        chg = float(chg_val)
        to = float(item.get("f8", 0)) if item.get("f8") != "-" and item.get("f8") is not None else 0.0
        vr = float(item.get("f10", 1.0)) if item.get("f10") != "-" and item.get("f10") is not None else 1.0
        mv_raw = item.get("f20")
        mv = (float(mv_raw) / 100000000.0) if mv_raw and mv_raw != "-" else 0.0 # 转为亿元
        pe_raw = item.get("f9")
        pe = float(pe_raw) if pe_raw and pe_raw != "-" else 0.0

        # 统计涨跌
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

        # 潜力股筛选逻辑打标
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

    # 北京时间
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

    # 写入 data.json
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 写入历史归档
    os.makedirs("history", exist_ok=True)
    with open(f"history/{date_str}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"数据全部生成完成！已收录 {len(stocks)} 只 A 股公司至 data.json。")

if __name__ == "__main__":
    main()
