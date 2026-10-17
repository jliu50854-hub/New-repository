import json
import os
import requests
from collections import defaultdict
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
    """获取三大指数实时点位与真实涨跌幅"""
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
    """抓取单页数据（包含行业板块 f100 与主力净流入 f62）"""
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
        # f12:代码, f14:名称, f2:现价, f3:涨跌幅, f8:换手率, f10:量比, f20:总市值, f9:PE, f62:主力净流入, f100:所属行业
        "fields": "f12,f14,f2,f3,f8,f10,f20,f9,f62,f100"
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
    """多线程并发拉取全市场 5400+ 只股票"""
    total_pages = 56
    all_records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_single_page, page) for page in range(1, total_pages + 1)]
        for f in as_completed(futures):
            res = f.result()
            if res:
                all_records.extend(res)
    return all_records

def evaluate_yesterday_performance(today_price_map):
    """计算上一期潜力股在今日的真实胜率与溢价"""
    summary = {"tracked_date": "暂无历史基准", "win_rate": "--", "avg_change": "--"}
    if not os.path.exists("data.json"):
        return summary
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            prev_data = json.load(f)
        prev_stocks = [s for s in prev_data.get("stocks", []) if s.get("tag") not in ("全市场", "涨停板")]
        if not prev_stocks:
            return summary
        gains = [today_price_map[s["code"]] for s in prev_stocks if s["code"] in today_price_map]
        if gains:
            wins = sum(1 for g in gains if g > 0)
            win_rate = (wins / len(gains)) * 100
            avg_chg = sum(gains) / len(gains)
            summary = {
                "tracked_date": prev_data.get("date", "上一交易日"),
                "win_rate": f"{win_rate:.1f}%",
                "avg_change": f"{avg_chg:+.2f}%"
            }
    except Exception as e:
        print(f"战绩回测跳过: {e}")
    return summary

def generate_ai_market_summary(up_count, down_count, limit_up, top_sectors, top_inflow_stocks):
    """智能生成每日盘面综述与策略建议"""
    total = up_count + down_count
    temp = int((up_count / total) * 100) if total > 0 else 50
    if temp >= 70:
        mood = f"情绪高涨（市场温度 {temp}°C），普涨格局明显，赚钱效应极佳"
        advice = "可积极围绕主线板块寻找放量突破标的，注意去弱留强。"
    elif temp >= 45:
        mood = f"情绪震荡分化（市场温度 {temp}°C），结构性行情为主"
        advice = "建议轻指数重个股，聚焦主力资金持续净流入的领涨行业，切忌盲目追高。"
    else:
        mood = f"情绪偏弱降温（市场温度 {temp}°C），亏钱效应有所扩大"
        advice = "建议控制仓位、防守为主，优先关注底部温和放量且估值合理的低位股。"

    sector_names = "、".join([f"【{s['name']}】({s['avg_change']:+.2f}%)" for s in top_sectors[:3]])
    inflow_names = "、".join([f"{s['name']}({s['net_inflow']})" for s in top_inflow_stocks[:3]])

    return (
        f"🤖 AI 盘面综述：今日全市场上涨 {up_count} 家，下跌 {down_count} 家，涨停 {limit_up} 家，{mood}。"
        f"资金主要进攻方向集中在 {sector_names or '暂无明显主线'}；"
        f"单日主力抢筹最明显的个股为 {inflow_names or '暂无'}。策略上，{advice}"
    )

def main():
    print("开始并发抓取全市场 A 股全维行情与资金流向...")
    raw_list = fetch_all_a_shares()
    indices = get_market_overview()

    stocks = []
    up_count, down_count, limit_up = 0, 0, 0
    seen_codes = set()
    today_price_map = {}
    sector_stats = defaultdict(lambda: {"count": 0, "chg_sum": 0.0, "inflow_sum": 0.0})

    for item in raw_list:
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)

        price_val, chg_val = item.get("f2"), item.get("f3")
        if price_val in ("-", None) or chg_val in ("-", None):
            continue

        try:
            price = float(price_val)
            chg = float(chg_val)
            to = float(item.get("f8", 0)) if item.get("f8") not in ("-", None) else 0.0
            vr = float(item.get("f10", 1.0)) if item.get("f10") not in ("-", None) else 1.0
            mv_raw = item.get("f20")
            mv = (float(mv_raw) / 100000000.0) if mv_raw not in ("-", None) else 0.0
            pe_raw = item.get("f9")
            pe = float(pe_raw) if pe_raw not in ("-", None) else 0.0
            inflow_raw = item.get("f62")
            # 转换主力净流入为“亿元”
            inflow = (float(inflow_raw) / 100000000.0) if inflow_raw not in ("-", None) else 0.0
            sector = str(item.get("f100", "其他")).strip()
            if not sector or sector == "-":
                sector = "其他"
        except (ValueError, TypeError):
            continue

        today_price_map[code] = chg

        # 统计涨跌与涨停
        is_limit_up = (chg >= 9.8 and not code.startswith(("30", "68", "8", "4", "92"))) or (chg >= 19.8)
        if chg > 0:
            up_count += 1
            if is_limit_up:
                limit_up += 1
        elif chg < 0:
            down_count += 1

        # 汇总行业板块表现
        if sector != "其他":
            sector_stats[sector]["count"] += 1
            sector_stats[sector]["chg_sum"] += chg
            sector_stats[sector]["inflow_sum"] += inflow

        symbol = f"sh{code}" if code.startswith("6") else (f"sz{code}" if code.startswith(("0", "3")) else f"bj{code}")
        is_st = "ST" in name or "退" in name

        # 多策略分类与 AI 简评生成
        tag = "全市场"
        comments = []
        if is_limit_up and not is_st:
            tag = "涨停板"
            comments.append(f"今日强势封涨停，换手{to:.1f}%")
        elif not is_st and price >= 4.0:
            if 3.0 <= chg <= 8.8 and 4.0 <= to <= 20.0 and vr >= 1.5 and inflow > 0.1:
                tag = "量价突破"
                comments.append(f"放量倍增(量比{vr:.1f})且主力净流入{inflow:+.2f}亿")
            elif 1.0 <= chg <= 4.5 and 2.5 <= to <= 10.0 and 1.2 <= vr <= 2.8 and inflow > 0:
                tag = "温和蓄势"
                comments.append(f"温和放量蓄势，主力暗中吸筹{inflow:+.2f}亿")
            elif to >= 12.0 and vr >= 1.5 and chg > 0:
                tag = "高换手博弈"
                comments.append(f"资金剧烈博弈(换手{to:.1f}%)")

        if inflow >= 1.0:
            comments.append(f"主力大幅抢筹{inflow:+.2f}亿")
        elif inflow <= -1.0:
            comments.append(f"主力净流出{inflow:.2f}亿")

        if 0 < pe <= 25:
            comments.append(f"估值偏低(PE:{pe:.1f})")
        elif pe > 100:
            comments.append("高估值注意波动")

        reason = "；".join(comments) if comments else f"所属{sector}，量比{vr:.1f}"
        mv_str = f"{mv:.1f}亿" if mv > 0 else "--"
        pe_str = f"{pe:.1f}" if pe > 0 else ("亏损" if pe < 0 else "--")
        inflow_str = f"{inflow:+.2f}亿"

        stocks.append({
            "code": code,
            "symbol": symbol,
            "name": name,
            "sector": sector,
            "price": f"{price:.2f}",
            "price_num": round(price, 2),
            "change": f"{chg:+.2f}%",
            "change_num": round(chg, 2),
            "turnover": f"{to:.2f}%",
            "turnover_num": round(to, 2),
            "volume_ratio": f"{vr:.2f}",
            "volume_ratio_num": round(vr, 2),
            "market_cap": mv_str,
            "market_cap_num": round(mv, 1),
            "pe": pe_str,
            "pe_num": round(pe, 1),
            "net_inflow": inflow_str,
            "net_inflow_num": round(inflow, 2),
            "tag": tag,
            "reason": reason
        })

    # 计算 Top 6 热门领涨行业板块（要求板块内至少有 8 只股票）
    top_sectors = []
    for sec_name, st in sector_stats.items():
        if st["count"] >= 8:
            avg_c = st["chg_sum"] / st["count"]
            top_sectors.append({
                "name": sec_name,
                "avg_change": round(avg_c, 2),
                "net_inflow": round(st["inflow_sum"], 2)
            })
    top_sectors.sort(key=lambda x: x["avg_change"], reverse=True)
    top_sectors = top_sectors[:6]

    # 按主力净流入排名前三的股票，用于生成 AI 综述
    top_inflow_stocks = sorted(stocks, key=lambda x: x["net_inflow_num"], reverse=True)[:3]

    # 默认按涨跌幅降序排列
    stocks.sort(key=lambda x: x["change_num"], reverse=True)

    # 昨日潜力股今日表现回测
    yesterday_perf = evaluate_yesterday_performance(today_price_map)

    # 生成 AI 盘面总结
    ai_summary = generate_ai_market_summary(up_count, down_count, limit_up, top_sectors, top_inflow_stocks)

    tz_bj = timezone(timedelta(hours=8))
    now = datetime.now(tz_bj)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "date": date_str,
        "update_time": time_str,
        "total_count": len(stocks),
        "ai_summary": ai_summary,
        "yesterday_performance": yesterday_perf,
        "top_sectors": top_sectors,
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

    # 存档历史文件与更新历史日期列表 (供前端历史时光机调用)
    os.makedirs("history", exist_ok=True)
    with open(f"history/{date_str}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    history_files = sorted(
        [f[:-5] for f in os.listdir("history") if f.endswith(".json") and f != "dates.json"],
        reverse=True
    )
    with open("history/dates.json", "w", encoding="utf-8") as f:
        json.dump(history_files, f, ensure_ascii=False, indent=2)

    print(f"全量数据生成成功！共收录 {len(stocks)} 只A股，已更新热门板块与历史时光机索引。")

if __name__ == "__main__":
    main()
