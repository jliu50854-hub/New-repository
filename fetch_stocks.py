import akshare as ak
import pandas as pd
import json
from datetime import datetime, timezone, timedelta

def main():
    print("开始抓取A股实时行情数据...")
    # 获取A股全市场实时行情
    df = ak.stock_zh_a_spot_em()

    # 1. 基础过滤：剔除 ST股、退市股、北交所（可根据喜好保留），剔除停牌或无价格股票
    df = df[~df['名称'].str.contains('ST|退')]
    df = df[~df['代码'].str.startswith(('8', '4'))]
    df = df.dropna(subset=['最新价', '涨跌幅', '换手率', '量比'])

    # 2. 策略一：【量价突破】（涨幅在 3%~8.5% 之间，未涨停但强势；换手率 5%~15% 资金活跃；量比 > 1.8 明显放量）
    cond_breakout = (
        (df['涨跌幅'] >= 3.0) & (df['涨跌幅'] <= 8.5) &
        (df['换手率'] >= 5.0) & (df['换手率'] <= 15.0) &
        (df['量比'] >= 1.8) &
        (df['最新价'] >= 5.0)
    )
    df_breakout = df[cond_breakout].sort_values(by='量比', ascending=False).head(15)

    # 3. 策略二：【活跃异动】（涨幅 1%~4% 温和蓄势，换手率 > 8%，量比 > 1.5）
    cond_active = (
        (df['涨跌幅'] >= 1.0) & (df['涨跌幅'] < 3.0) &
        (df['换手率'] >= 8.0) & (df['换手率'] <= 20.0) &
        (df['量比'] >= 1.5) &
        (df['最新价'] >= 5.0)
    )
    df_active = df[cond_active].sort_values(by='换手率', ascending=False).head(15)

    stocks = []
    
    for _, row in df_breakout.iterrows():
        stocks.append({
            "code": str(row['代码']),
            "name": str(row['名称']),
            "price": f"{row['最新价']:.2f}",
            "change": f"+{row['涨跌幅']:.2f}%",
            "change_num": float(row['涨跌幅']),
            "turnover": f"{row['换手率']:.2f}%",
            "volume_ratio": f"{row['量比']:.2f}",
            "tag": "量价突破",
            "reason": f"今日放量上攻（量比{row['量比']:.1f}），换手充分（{row['换手率']:.1f}%），资金介入明显"
        })

    for _, row in df_active.iterrows():
        stocks.append({
            "code": str(row['代码']),
            "name": str(row['名称']),
            "price": f"{row['最新价']:.2f}",
            "change": f"+{row['涨跌幅']:.2f}%",
            "change_num": float(row['涨跌幅']),
            "turnover": f"{row['换手率']:.2f}%",
            "volume_ratio": f"{row['量比']:.2f}",
            "tag": "活跃异动",
            "reason": f"温和上涨蓄势，高换手率（{row['换手率']:.1f}%）伴随放量，存在异动潜力"
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
    print(f"成功筛选出 {len(stocks)} 只潜力股，已写入 data.json")

if __name__ == "__main__":
    main()
