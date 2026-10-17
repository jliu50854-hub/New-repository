import json
import os
import time
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
    """获取大盘主要指数与涨跌情绪看板"""
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

    # 获取全市场涨跌统计估算
    up_count, down_count, limit_up = 0, 0, 0
    try:
        s_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        for page in [1, 2, 3]:
            res = requests.get(s_url, params={"page": page, "num": 100, "sort": "changepercent", "asc": 0, "node": "hs_a"}, headers=HEADERS, timeout=8).json()
            for s in res:
                chg = float(s.get("changepercent", 0))
                if chg >= 9.8:
                    limit_up += 1
                if chg > 0:
                    up_count += 1
                elif chg < 0:
                    down_count += 1
    except Exception:
        pass

    return {
        "indices": indices,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up": limit_up
    }

def fetch_sina_candidates():
    """多维度拉取候选股票池"""
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    candidates = {}
    tasks = [
        ("turnoverratio", range(1, 6)),  # 换手率最活跃标的
        ("changepercent", range(2, 6)),  # 涨幅居前标的
        ("amount", range(1, 4))          # 成交金额大票
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
                time.sleep(0.2)
            except Exception as e:
                print(f"分片拉取跳过 ({sort_field}-第{page}页): {e}")

    return list(candidates.values())

def fetch_tencent_details(symbols):
    """批量补全腾讯行情接口数据：量比、总市值、市盈率 PE"""
    details = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
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
                        vr = float(parts[49]) if parts[49] else 1.0
                        total_mv = float(parts[45]) if len(parts) > 45 and parts[45] else 0.0  # 总市值(亿元)
                        pe = float(parts[39]) if len(parts) > 39 and parts[39] else 0.0        # 动态市盈率
                    except (ValueError, IndexError):
                        vr, total_mv, pe = 1.0, 0.0, 0.0
                    details[code] = {
                        "volume_ratio": vr,
                        "market_cap": total_mv,
                        "pe": pe
                    }
        except Exception as e:
            print(f"拉取细节指标跳过: {e}")
    return details

def evaluate_yesterday_performance(today_stocks_map):
    """
    战绩追踪：读取上一次筛选的股票池，对比今日涨幅，计算胜率与平均收益
    """
    summary = {"tracked_date": None, "win_rate": "--", "avg_change": "--", "total_count": 0}
    if not os.path.exists("data.json"):
        return summary

    try:
        with open("data.json", "r", encoding="utf-8") as f:
            prev_data = json.load(f)
        prev_stocks = prev_data.get("stocks", [])
        if not prev_stocks:
            return summary

        tracked_date = prev_data.get("date", "上一交易日")
        gains = []
        for s in prev_stocks:
            code = s["code"]
            if code in today_stocks_map:
                today_chg = today_stocks_map[code]
                gains.append(today_chg)

        if gains:
            wins = sum(1 for g in gains if g > 0)
            win_rate = (wins / len(gains)) * 100
            avg_change = sum(gains) / len(gains)
            summary = {
                "tracked_date": tracked_date,
                "win_rate": f"{win_rate:.1f}%",
                "avg_change": f"{avg_change:+.2f}%",
                "total_count": len(gains)
            }
    except Exception as e:
        print(f"计算战绩出错: {e}")

    return summary

def send_webhook_notification(today_date, stocks, summary):
    """可选消息推送：支持 Server酱 / PushPlus / 钉钉 / 飞书机器人"""
    webhook_url = os.environ.get("NOTIFY_WEBHOOK", "").strip()
    if not webhook_url:
        return

    top5 = stocks[:5]
    stock_lines = "\n".join([f"- **{s['name']}** ({s['code']}): {s['change']} | {s['tag']} (量比:{s['volume_ratio']})" for s in top5])
    content = (
        f"### 📈 A股潜力股自动复盘 ({today_date})\n\n"
        f"**昨日选股次日战绩**：胜率 {summary.get('win_rate')}，平均涨幅 {summary.get('avg_change')}\n\n"
        f"**今日精选标的 Top 5**：\n{stock_lines}\n\n"
        f"[点击查看完整雷达网页详情]({os.environ.get('PAGE_URL', '#')})"
    )

    try:
        # 兼容钉钉/飞书 markdown 格式，或 PushPlus json
        if "dingtalk" in webhook_url:
            payload = {"msgtype": "markdown", "markdown": {"title": "A股潜力雷达更新", "text": content}}
        elif "pushplus" in webhook_url:
            payload = {"title": f"A股每日潜力股 ({today_date})", "content": content, "template": "markdown"}
        else:
            payload = {"text": content}
        requests.post(webhook_url, json=payload, timeout=10)
        print("Webhook 推送成功！")
    except Exception as e:
        print(f"推送通知跳过: {e}")

def main():
    print("开始获取市场全量行情与情绪看板...")
    overview = get_market_overview()
    raw_list = fetch_sina_candidates()
    print(f"拉取到 {len(raw_list)} 只备选个股，正在补齐量比与估值...")

    symbols = [str(item.get("symbol", "")) for item in raw_list if str(item.get("symbol", ""))]
    details_map = fetch_tencent_details(symbols)

    today_price_map = {}
    valid_stocks = []

    for item in raw_list:
        name = str(item.get("name", ""))
        code = str(item.get("code", ""))
        symbol = str(item.get("symbol", ""))

        try:
            price = float(item.get("trade", 0))
            change = float(item.get("changepercent", 0))
            turnover = float(item.get("turnoverratio", 0))
        except (ValueError, TypeError):
            continue

        today_price_map[code] = change

        # 基础过滤：剔除ST、退市、微盘僵尸股、亏损或极端标的
        if "ST" in name or "退" in name or code.startswith(("8", "4", "92")):
            continue
        if price < 4.0 or turnover < 2.0:
            continue

        detail = details_map.get(code, {"volume_ratio": 1.0, "market_cap": 0.0, "pe": 0.0})
        mv = detail["market_cap"]
        vr = detail["volume_ratio"]
        pe = detail["pe"]

        # 市值处于 30亿 ~ 1500亿 适中区间
        if mv > 0 and (mv < 30.0 or mv > 1500.0):
            continue

        item["_price"] = price
        item["_change"] = change
        item["_turnover"] = turnover
        item["_vr"] = vr
        item["_mv"] = mv
        item["_pe"] = pe
        item["_symbol"] = symbol
        valid_stocks.append(item)

    # 策略 1：量价突破（放量拉升、资金进场）
    strat_breakout = [
        s for s in valid_stocks 
        if 3.0 <= s["_change"] <= 8.5 and 5.0 <= s["_turnover"] <= 18.0 and s["_vr"] >= 1.6
    ]
    strat_breakout.sort(key=lambda x: x["_vr"], reverse=True)

    # 策略 2：底部温和放量（涨幅稳健、量比大于 1.3）
    strat_bottom = [
        s for s in valid_stocks 
        if 1.5 <= s["_change"] <= 4.0 and 3.0 <= s["_turnover"] <= 9.0 and 1.3 <= s["_vr"] <= 2.5
    ]
    strat_bottom.sort(key=lambda x: x["_turnover"], reverse=True)

    # 策略 3：高活跃异动（高换手博弈标的）
    strat_active = [
        s for s in valid_stocks 
        if s["_turnover"] >= 10.0 and s["_vr"] >= 1.4 and s["_change"] > 0
    ]
    strat_active.sort(key=lambda x: x["_turnover"], reverse=True)

    formatted_stocks = []
    seen = set()

    def add_items(source_list, tag, reason_fmt, limit=12):
        count = 0
        for s in source_list:
            if s["code"] in seen:
                continue
            seen.add(s["code"])
            mv_str = f"{s['_mv']:.1f}亿" if s["_mv"] > 0 else "--"
            pe_str = f"{s['_pe']:.1f}" if s["_pe"] > 0 else "亏损"
            formatted_stocks.append({
                "code": s["code"],
                "symbol": s["_symbol"],
                "name": s["name"],
                "price": f"{s['_price']:.2f}",
                "change": f"{s['_change']:+.2f}%",
                "change_num": round(s["_change"], 2),
                "turnover": f"{s['_turnover']:.2f}%",
                "turnover_num": s["_turnover"],
                "volume_ratio": f"{s['_vr']:.2f}",
                "volume_ratio_num": s["_vr"],
                "market_cap": mv_str,
                "market_cap_num": s["_mv"],
                "pe": pe_str,
                "tag": tag,
                "reason": reason_fmt.format(vr=s["_vr"], to=s["_turnover"], mv=mv_str, pe=pe_str)
            })
            count += 1
            if count >= limit:
                break

    add_items(strat_breakout, "量价突破", "放量起爆（量比{vr:.1f}，换手{to:.1f}%），市值{mv}，资金主升特征明显")
    add_items(strat_bottom, "温和蓄势", "底部温和放大，换手率{to:.1f}%，量比{vr:.1f}，回撤风险较低")
    add_items(strat_active, "高换手博弈", "短线活跃资金博弈（换手{to:.1f}%），量比{vr:.1f}，关注盘中承接")

    # 计算昨日胜率与次日表现
    performance_summary = evaluate_yesterday_performance(today_price_map)

    # 存档历史记录
    tz_bj = timezone(timedelta(hours=8))
    now = datetime.now(tz_bj)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "date": date_str,
        "update_time": time_str,
        "market_overview": overview,
        "yesterday_performance": performance_summary,
        "stocks": formatted_stocks
    }

    # 写入当前数据
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 存档到 history 文件夹
    os.makedirs("history", exist_ok=True)
    with open(f"history/{date_str}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 执行 Webhook 消息推送（如果配置了环境变量）
    send_webhook_notification(date_str, formatted_stocks, performance_summary)
    print(f"数据生成完成！共入选 {len(formatted_stocks)} 只标的，历史记录与战绩均已存档。")

if __name__ == "__main__":
    main()
