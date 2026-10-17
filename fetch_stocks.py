import json
import os
import time
import requests
from collections import defaultdict
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

def get_all_stock_symbols():
    """从新浪财经稳健获取全市场 5300+ 只A股的代码列表"""
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    symbols = []
    seen = set()

    for page in range(1, 60):
        params = {
            "page": page,
            "num": 100,
            "sort": "symbol",
            "asc": 1,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page"
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
            if resp.status_code == 200 and resp.text and resp.text != "null":
                data = resp.json()
                if not data or not isinstance(data, list):
                    break
                for item in data:
                    sym = str(item.get("symbol", "")).strip()
                    if sym and sym not in seen:
                        seen.add(sym)
                        symbols.append(sym)
                if len(data) < 100:
                    break
            else:
                break
        except Exception as e:
            print(f"新浪列表第 {page} 页重试: {e}")
        time.sleep(0.1)

    print(f"已从新浪获取 {len(symbols)} 只A股代码！")
    return symbols

def fetch_all_quotes_from_tencent(symbols):
    """通过腾讯财经CDN接口批量拉取全部股票的完整行情"""
    stocks_raw = []
    batch_size = 80
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            resp.encoding = "gbk"
            for line in resp.text.strip().split(";"):
                if "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) > 50:
                    stocks_raw.append(parts)
        except Exception as e:
            print(f"腾讯批次 {i} 拉取跳过: {e}")
        time.sleep(0.05)
    return stocks_raw

def analyze_single_kline(open_p, close_p, high_p, low_p, pre_close, chg, to):
    """根据当日开盘、收盘、最高、最低价自动识别经典K线形态并给出解读"""
    if high_p <= low_p or open_p <= 0:
        return "普通K线", "窄幅波动"

    total_len = high_p - low_p
    body = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p
    amplitude = (total_len / pre_close * 100) if pre_close > 0 else 0

    if close_p > open_p and chg >= 4.0 and upper_shadow <= total_len * 0.08 and lower_shadow <= total_len * 0.08:
        return "光头大阳", "全天买盘逼空上行，多头力量极强，关注次日溢价"
    if amplitude >= 3.0 and lower_shadow >= total_len * 0.55 and upper_shadow <= total_len * 0.15:
        if close_p >= open_p:
            return "金针探底", "日内深V反转，下方承接力极强，主力洗盘特征明显"
        else:
            return "长下影线", "盘中恐慌盘涌出后获资金抄底承接，关注下影线低点支撑"
    if amplitude >= 3.5 and upper_shadow >= total_len * 0.55 and lower_shadow <= total_len * 0.15:
        if chg > 0 and to < 12.0:
            return "仙人指路", "冲高回落留长上影，温和换手疑似主力试盘测试上方抛压"
        else:
            return "长上影线", "上方抛压较重，冲高受阻回落，短线需突破上影最高价才可转强"
    if amplitude >= 2.5 and body <= total_len * 0.12:
        return "变盘十字星", "多空分歧加剧但势均力敌，面临短线方向选择"
    if open_p > pre_close * 1.02 and close_p < open_p * 0.97:
        return "高开低走", "高开后遭遇获利盘集中兑现，日内套牢盘较重，谨防调整"
    if 1.0 <= chg < 4.0 and close_p > open_p and upper_shadow < body:
        return "稳健推升阳线", "重心温和上移，多头占优且抛压可控"

    return "常规形态", "量价平稳运行"

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
    except Exception:
        pass
    return summary

def generate_ai_market_summary(up_count, down_count, limit_up, top_inflow_stocks):
    """智能生成每日盘面综述与策略建议"""
    total = up_count + down_count
    temp = int((up_count / total) * 100) if total > 0 else 50
    if temp >= 70:
        mood = f"情绪高涨（市场温度 {temp}°C），普涨格局明显，赚钱效应极佳"
        advice = "可积极围绕主线寻找放量突破标的，注意去弱留强。"
    elif temp >= 45:
        mood = f"情绪震荡分化（市场温度 {temp}°C），结构性行情为主"
        advice = "建议轻指数重个股，聚焦放量且高换手的活跃个股，切忌盲目追高。"
    else:
        mood = f"情绪偏弱降温（市场温度 {temp}°C），亏钱效应有所扩大"
        advice = "建议控制仓位、防守为主，优先关注底部金针探底或温和蓄势的低位股。"

    active_names = "、".join([f"{s['name']}({s['change']})" for s in top_inflow_stocks[:4]])
    return (
        f"🤖 AI 盘面综述：今日全市场共收录 {total} 只交易A股，其中上涨 {up_count} 家，下跌 {down_count} 家，涨停 {limit_up} 家，{mood}。"
        f"盘面资金最活跃的核心标的包括 {active_names or '暂无'}。策略上，{advice}"
    )

def main():
    print("开始全量抓取 A 股 5000+ 公司数据与K线形态识别...")
    symbols = get_all_stock_symbols()
    raw_quotes = fetch_all_quotes_from_tencent(symbols)
    print(f"腾讯接口成功返回 {len(raw_quotes)} 只股票的完整行情！")

    indices = get_market_overview()
    stocks = []
    up_count, down_count, limit_up = 0, 0, 0
    today_price_map = {}
    board_stats = defaultdict(lambda: {"count": 0, "chg_sum": 0.0, "amount_sum": 0.0})

    for parts in raw_quotes:
        try:
            name = parts[1].strip()
            code = parts[2].strip()
            price = float(parts[3]) if parts[3] else 0.0
            pre_close = float(parts[4]) if parts[4] else 0.0
            open_p = float(parts[5]) if parts[5] else price
            high_p = float(parts[33]) if parts[33] else price
            low_p = float(parts[34]) if parts[34] else price
            if price <= 0 or pre_close <= 0:
                continue

            chg = float(parts[32]) if parts[32] else 0.0
            to = float(parts[38]) if parts[38] else 0.0
            pe = float(parts[39]) if parts[39] else 0.0
            mv = float(parts[45]) if parts[45] else 0.0
            vr = float(parts[49]) if parts[49] else 1.0
            amount_wan = float(parts[37]) if parts[37] else 0.0
            amount_yi = amount_wan / 10000.0
        except (ValueError, IndexError):
            continue

        today_price_map[code] = chg

        if code.startswith("688"):
            sector, symbol = "科创板", f"sh{code}"
        elif code.startswith("6"):
            sector, symbol = "沪市主板", f"sh{code}"
        elif code.startswith("30"):
            sector, symbol = "创业板", f"sz{code}"
        elif code.startswith(("00", "0")):
            sector, symbol = "深市主板", f"sz{code}"
        else:
            sector, symbol = "北交所", f"bj{code}"

        is_limit_up = (chg >= 9.8 and not code.startswith(("30", "68", "8", "4", "92"))) or (chg >= 19.8)
        if chg > 0:
            up_count += 1
            if is_limit_up:
                limit_up += 1
        elif chg < 0:
            down_count += 1

        board_stats[sector]["count"] += 1
        board_stats[sector]["chg_sum"] += chg
        board_stats[sector]["amount_sum"] += amount_yi

        is_st = "ST" in name or "退" in name

        kline_pattern, kline_desc = analyze_single_kline(open_p, price, high_p, low_p, pre_close, chg, to)

        tag = "全市场"
        comments = [f"【{kline_pattern}】{kline_desc}"]
        if is_limit_up and not is_st:
            tag = "涨停板"
            comments.insert(0, f"强势封涨停(换手{to:.1f}%)")
        elif not is_st and price >= 4.0:
            if 3.0 <= chg <= 8.8 and 4.0 <= to <= 20.0 and vr >= 1.5:
                tag = "量价突破"
                comments.insert(0, f"放量上攻(量比{vr:.1f})")
            elif 1.0 <= chg <= 4.5 and 2.5 <= to <= 10.0 and 1.2 <= vr <= 2.8:
                tag = "温和蓄势"
                comments.insert(0, f"温和放量蓄势(量比{vr:.1f})")
            elif to >= 12.0 and vr >= 1.4 and chg > 0:
                tag = "高换手博弈"
                comments.insert(0, f"短线剧烈博弈(换手{to:.1f}%)")

        reason = "；".join(comments)
        mv_str = f"{mv:.1f}亿" if mv > 0 else "--"
        pe_str = f"{pe:.1f}" if pe > 0 else ("亏损" if pe < 0 else "--")
        amount_str = f"{amount_yi:.2f}亿"

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
            "net_inflow": amount_str,
            "net_inflow_num": round(amount_yi, 2),
            "kline_pattern": kline_pattern,
            "tag": tag,
            "reason": reason
        })

    top_sectors = []
    for sec_name, st in board_stats.items():
        if st["count"] > 0:
            avg_c = st["chg_sum"] / st["count"]
            top_sectors.append({
                "name": sec_name,
                "avg_change": round(avg_c, 2),
                "net_inflow": round(st["amount_sum"], 1)
            })
    top_sectors.sort(key=lambda x: x["avg_change"], reverse=True)
    stocks.sort(key=lambda x: x["change_num"], reverse=True)

    yesterday_perf = evaluate_yesterday_performance(today_price_map)
    top_active = [s for s in stocks if s["tag"] == "量价突破"][:4]
    ai_summary = generate_ai_market_summary(up_count, down_count, limit_up, top_active)

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

    os.makedirs("history", exist_ok=True)
    with open(f"history/{date_str}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    history_files = sorted(
        [f[:-5] for f in os.listdir("history") if f.endswith(".json") and f != "dates.json"],
        reverse=True
    )
    with open("history/dates.json", "w", encoding="utf-8") as f:
        json.dump(history_files, f, ensure_ascii=False, indent=2)

    print(f"大功告成！全市场共收录 {len(stocks)} 只A股及K线形态解读！")

if __name__ == "__main__":
    main()
