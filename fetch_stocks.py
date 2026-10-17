import json
import time
import requests
from datetime import datetime, timezone, timedelta

# 伪装浏览器请求头，防止被接口拦截
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/"
}

def fetch_sina_candidates():
    """
    从新浪财经行情中心拉取换手率活跃及涨幅居前的A股候选池（对GitHub海外IP极度友好）
    """
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    candidates = {}

    # 分别按【换手率】和【涨跌幅】拉取前几页最活跃的股票，避免全量翻页被封
    tasks = [
        ("turnoverratio", range(1, 6)),  # 换手率排名前 400
        ("changepercent", range(2, 7)),  # 避开已涨停一字板，取涨幅前列股票
    ]

    for sort_field, pages in tasks:
        for page in pages:
            params = {
                "page": page,
                "num": 80,
                "sort": sort_field,
                "asc": 0,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page"
            }
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
                if resp.status_code == 200 and resp.text:
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            code = str(item.get("code", ""))
                            candidates[code] = item
                time.sleep(0.3)
            except Exception as e:
                print(f"拉取新浪行情分片 ({sort_field}-第{page}页) 跳过: {e}")

    return list(candidates.values())

def fetch_tencent_volume_ratio(symbols):
    """
    通过腾讯财经CDN接口批量补全候选股票的【量比】指标
    """
    vol_ratios = {}
    batch_size = 40
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        q_str = ",".join(batch)
        url = f"http://qt.gtimg.cn/q={q_str}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.encoding = "gbk"
            for line in resp.text.strip().split(";"):
                if "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) > 49:
                    code = parts[2]
                    try:
                        vol_ratios[code] = float(parts[49])
                    except ValueError:
                        vol_ratios[code] = 1.0
        except Exception as e:
            print(f"获取腾讯量比数据跳过: {e}")
    return vol_ratios

def main():
    print("开始通过新浪+腾讯双通道抓取A股行情数据...")
    raw_list = fetch_sina_candidates()
    print(f"成功获取 {len(raw_list)} 只活跃A股候选数据，正在筛选...")

    # 1. 基础过滤：剔除 ST股、退市股、北交所（8/4/92开头）及低价股
    filtered = []
    symbols_to_query = []
    for item in raw_list:
        name = str(item.get("name", ""))
        code = str(item.get("code", ""))
        symbol = str(item.get("symbol", ""))

        if "ST" in name or "退" in name or code.startswith(("8", "4", "92")):
            continue

        try:
            price = float(item.get("trade", 0))
            change = float(item.get("changepercent", 0))
            turnover = float(item.get("turnoverratio", 0))
        except (ValueError, TypeError):
            continue

        # 仅保留价格 >= 5元、当日上涨且未涨停封死（1% ~ 8.8%）、换手率 >= 4% 的活跃标的
        if price >= 5.0 and 1.0 <= change <= 8.8 and turnover >= 4.0:
            item["_price"] = price
            item["_change"] = change
            item["_turnover"] = turnover
            filtered.append(item)
            if symbol:
                symbols_to_query.append(symbol)

    # 2. 批量获取精确量比
    vol_ratios = fetch_tencent_volume_ratio(symbols_to_query)

    breakout_list = []
    active_list = []

    for item in filtered:
        code = str(item.get("code", ""))
        vr = vol_ratios.get(code, 1.5)
        item["_vr"] = vr
        change = item["_change"]
        turnover = item["_turnover"]

        # 策略一：【量价突破】（涨幅 3.0%~8.5%，换手率 5%~18%，量比 >= 1.5）
        if 3.0 <= change <= 8.5 and 5.0 <= turnover <= 18.0 and vr >= 1.5:
            breakout_list.append(item)
        # 策略二：【活跃异动】（涨幅 1.0%~3.0% 温和蓄势，换手率 >= 6%，量比 >= 1.2）
        elif 1.0 <= change < 3.0 and 6.0 <= turnover <= 25.0 and vr >= 1.2:
            active_list.append(item)

    # 按量比和换手率排序，各取前 15 只
    breakout_list.sort(key=lambda x: x["_vr"], reverse=True)
    active_list.sort(key=lambda x: x["_turnover"], reverse=True)

    stocks = []
    for row in breakout_list[:15]:
        stocks.append({
            "code": str(row["code"]),
            "name": str(row["name"]),
            "price": f"{row['_price']:.2f}",
            "change": f"+{row['_change']:.2f}%",
            "change_num": round(row["_change"], 2),
            "turnover": f"{row['_turnover']:.2f}%",
            "volume_ratio": f"{row['_vr']:.2f}",
            "tag": "量价突破",
            "reason": f"放量上攻（量比{row['_vr']:.1f}），换手充分（{row['_turnover']:.1f}%），资金介入明显"
        })

    for row in active_list[:15]:
        stocks.append({
            "code": str(row["code"]),
            "name": str(row["name"]),
            "price": f"{row['_price']:.2f}",
            "change": f"+{row['_change']:.2f}%",
            "change_num": round(row["_change"], 2),
            "turnover": f"{row['_turnover']:.2f}%",
            "volume_ratio": f"{row['_vr']:.2f}",
            "tag": "活跃异动",
            "reason": f"温和上涨蓄势，高换手率（{row['_turnover']:.1f}%）伴随放量，存在异动潜力"
        })

    # 生成北京时间
    tz_bj = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "update_time": now_str,
        "stocks": stocks
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"成功筛选出 {len(stocks)} 只潜力股，已写入 data.json！")

if __name__ == "__main__":
    main()
