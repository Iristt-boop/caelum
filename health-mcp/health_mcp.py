"""
健康数据 MCP Server
把 VPS 上存的健康数据暴露成 Agent 可调用的工具。

启动方式:
    # HTTP 模式（VPS 上，给同机的 Nox Core 调）
    python health_mcp.py --http        # → http://127.0.0.1:8101/mcp

    # stdio 本地模式 (Claude Desktop 直接起进程)
    python health_mcp.py

⚠️ 传输用 **streamable-http，不是 sse**：
Nox Core 的 `tools/mcp_client.py` 只实现了 streamable-http
（`from mcp.client.streamable_http import streamablehttp_client`），
OB 和 ha-mcp 也都是这个。这边要是继续用 sse，Core 根本连不上，
而且得为它单开一套客户端代码 —— 一种传输一套代码，别开第二条。
"""

import argparse
import json
import os
import sqlite3

from mcp.server.fastmcp import FastMCP

DB_PATH = os.getenv("HEALTH_DB", "/root/data/health.db")
PORT = int(os.getenv("HEALTH_MCP_PORT", "8101"))
# 只听本机。它只给同机的 Nox Core 调，没有自己的鉴权层 ——
# 绑 0.0.0.0 就等于把糖糖的健康数据摆在公网上，只剩防火墙一道门。
# （老机上 3003 / 8123 就是这么裸奔的，幸好被安全组挡着，见第二十五节。）
HOST = os.getenv("HEALTH_MCP_HOST", "127.0.0.1")

mcp = FastMCP("health-data", host=HOST, port=PORT)
# 端点路径设成 /mcp，和 OB、ha-mcp、app-tracker 保持一致
mcp.settings.streamable_http_path = "/mcp"

# 同一天可能有多行：快捷指令重传一次、手动测试一次，就是三行。
# 统计必须按天去重，否则：
#   - LIMIT 7 取的是「7 行」不是「7 天」，可能只覆盖 3 天
#   - AVG(steps) 会把传了 3 次的那天算成 3 倍权重
# 取每天 id 最大的那条（最后传上来的那次）。
_LATEST_PER_DAY = "id IN (SELECT MAX(id) FROM health GROUP BY date)"


def query_db(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@mcp.tool()
def get_today_health() -> str:
    """获取今日健康数据:步数、心率、卡路里、距离等(快捷指令通常传的是昨日数据,今日数据可能不完整)"""
    rows = query_db(
        "SELECT * FROM health WHERE date = date('now','localtime') ORDER BY created_at DESC LIMIT 1"
    )
    if not rows:
        return "今日暂无数据(快捷指令每天传一次,通常传的是昨天数据)"
    return json.dumps(rows[0], ensure_ascii=False)


@mcp.tool()
def get_latest_health() -> str:
    """获取最近一条完整健康数据。

    ⚠️ 里面有**两个日期**，别混：
      date       —— 步数/心率/能量所属的自然日（通常是昨天）
      sleep_date —— 这一觉的归属日，**Apple 按醒来那天算**，通常是今天

    说睡眠按 sleep_date，说活动按 date。两者差一天是正常的，不是数据错了。

    另外这两个日期都可能不是「昨天/今天」—— 她哪天没同步，
    拿到的就是更早的记录。**按字段值说，别默认说成昨天。**
    """
    rows = query_db("SELECT * FROM health ORDER BY date DESC, created_at DESC LIMIT 1")
    if not rows:
        return "暂无数据"
    return json.dumps(rows[0], ensure_ascii=False)


@mcp.tool()
def get_latest_sleep() -> str:
    """获取最近一晚睡眠:总时长、深睡、REM、浅睡、清醒时间。

    ⚠️ 看「睡眠日期」字段，**不要看「活动数据日期」** —— 两者差一天：
    Apple 把睡眠归到醒来那天，而同一条记录里的步数/心率是前一个自然日的。
    说「昨晚睡了多久」时按「睡眠日期」说。
    """
    rows = query_db("""
        SELECT date, sleep_date, sleep_duration_min, deep_sleep_min,
               rem_sleep_min, core_sleep_min, awake_min
        FROM health WHERE sleep_duration_min IS NOT NULL
        ORDER BY COALESCE(sleep_date, date) DESC, id DESC LIMIT 1
    """)
    if not rows:
        return "暂无睡眠数据"
    r = rows[0]

    def h(m):
        return f"{int(m//60)}小时{int(m%60)}分钟" if m else "无"

    return json.dumps({
        "睡眠日期": r["sleep_date"] or f"{r['date']}（老数据，没记归属日，实际是这天早上醒的前一觉）",
        "活动数据日期": r["date"],
        "总睡眠": h(r["sleep_duration_min"]),
        "深度睡眠": h(r["deep_sleep_min"]),
        "REM睡眠": h(r["rem_sleep_min"]),
        "浅睡": h(r["core_sleep_min"]),
        "睡眠中清醒": h(r["awake_min"]),
        "深睡占比": f"{r['deep_sleep_min']/r['sleep_duration_min']*100:.1f}%" if r["deep_sleep_min"] and r["sleep_duration_min"] else "无",
    }, ensure_ascii=False)


@mcp.tool()
def get_health_history(days: int = 7) -> str:
    """获取最近 N 天健康趋势:步数、心率、睡眠(默认 7 天)。每天只取最后同步的那条。"""
    rows = query_db(f"""
        SELECT date, steps, resting_heart_rate, hrv_ms, sleep_duration_min, active_energy
        FROM health WHERE {_LATEST_PER_DAY}
        ORDER BY date DESC LIMIT ?
    """, (days,))
    if not rows:
        return "暂无历史数据"
    return json.dumps(rows, ensure_ascii=False)


@mcp.tool()
def check_health_warnings() -> str:
    """检查最近 3 天的异常指标:静息心率偏高、睡眠不足、活动量过低、HRV 骤降"""
    rows = query_db(f"""
        SELECT date, resting_heart_rate, hrv_ms, sleep_duration_min, steps
        FROM health WHERE {_LATEST_PER_DAY}
        ORDER BY date DESC LIMIT 3
    """)
    if not rows:
        return "暂无数据"
    warnings = []
    for r in rows:
        d = r["date"]
        if r.get("resting_heart_rate") and r["resting_heart_rate"] > 80:
            warnings.append(f"{d}: 静息心率偏高 {r['resting_heart_rate']:.0f} bpm")
        if r.get("hrv_ms") and r["hrv_ms"] < 25:
            warnings.append(f"{d}: HRV 偏低 {r['hrv_ms']:.0f} ms,可能疲劳/压力大")
        if r.get("sleep_duration_min") and r["sleep_duration_min"] < 360:
            warnings.append(f"{d}: 睡眠不足 {r['sleep_duration_min']:.0f} 分钟")
        if r.get("steps") and r["steps"] < 3000:
            warnings.append(f"{d}: 活动量过低 {r['steps']} 步")
    return json.dumps(warnings if warnings else ["最近 3 天指标正常"], ensure_ascii=False)


@mcp.tool()
def get_menstrual_cycle() -> str:
    """查糖糖当前经期状态：第几天、周期长度、预计下次日期。
    数据来自快捷指令同步的 Apple Health 经期记录。"""
    rows = query_db("""
        SELECT date, flow_level, day_number, cycle_start
        FROM menstrual
        ORDER BY date DESC LIMIT 40
    """)
    if not rows:
        return "暂无经期数据"

    # 找当前周期
    latest = rows[0]
    cs = latest.get("cycle_start") or latest["date"]

    # 这个周期里最早和最晚的日期
    cycle_dates = [r["date"] for r in rows if r.get("cycle_start") == cs]
    if cycle_dates:
        first = min(cycle_dates)
        last = max(cycle_dates)
    else:
        first = last = latest["date"]

    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y-%m-%d")
    day = (_dt.strptime(today_str, "%Y-%m-%d") - _dt.strptime(first, "%Y-%m-%d")).days + 1

    # 预测：取最近 3 个周期的平均长度
    cycle_starts = []
    seen = set()
    for r in rows:
        cs_val = r.get("cycle_start")
        if cs_val and cs_val not in seen:
            seen.add(cs_val)
            cycle_starts.append(cs_val)
    cycle_starts.sort()
    avg_len = 35  # 默认
    if len(cycle_starts) >= 2:
        lengths = []
        for i in range(1, min(len(cycle_starts), 4)):
            d1 = _dt.strptime(cycle_starts[i], "%Y-%m-%d")
            d0 = _dt.strptime(cycle_starts[i-1], "%Y-%m-%d")
            lengths.append((d1 - d0).days)
        if lengths:
            avg_len = int(sum(lengths) / len(lengths))
    elif len(cycle_starts) == 1:
        avg_len = 35

    # 下次预测
    next_date = _dt.strptime(first, "%Y-%m-%d")
    from datetime import timedelta
    next_start = next_date + timedelta(days=avg_len)
    days_until = (next_start - _dt.now()).days

    # 最近几天的流量
    recent = []
    for r in rows[:7]:
        recent.append(f"{r['date']} Day{r['day_number']} {r['flow_level'] or '无'}")

    import json
    return json.dumps({
        "今天周期第几天": day,
        "周期起点": first,
        "最近带血日": last,
        "平均周期": f"{avg_len} 天",
        "预计下次": next_start.strftime("%Y-%m-%d"),
        "距离下次": f"{days_until} 天",
        "最近记录": recent,
    }, ensure_ascii=False)


@mcp.tool()
def get_weekly_summary() -> str:
    """最近 7 天汇总:日均步数、平均睡眠、心率范围,适合周复盘"""
    rows = query_db(f"""
        SELECT
            ROUND(AVG(steps), 0) as avg_steps,
            MAX(steps) as max_steps,
            MIN(steps) as min_steps,
            ROUND(AVG(sleep_duration_min), 0) as avg_sleep_min,
            ROUND(AVG(resting_heart_rate), 1) as avg_rhr,
            ROUND(AVG(hrv_ms), 1) as avg_hrv,
            ROUND(SUM(active_energy), 0) as total_energy,
            COUNT(*) as days
        FROM health
        WHERE date >= date('now','localtime','-7 days') AND {_LATEST_PER_DAY}
    """)
    if not rows or rows[0]["avg_steps"] is None:
        return "近 7 天数据不足"
    r = rows[0]
    return json.dumps({
        # 明说这是几天的平均 —— 她可能只同步了 3 天，那「7 天平均」就是假的
        "统计天数": r["days"],
        "日均步数": r["avg_steps"],
        "最多一天": r["max_steps"],
        "最少一天": r["min_steps"],
        "平均睡眠": f"{int(r['avg_sleep_min']//60)}小时{int(r['avg_sleep_min']%60)}分钟" if r["avg_sleep_min"] else "无",
        "平均静息心率": r["avg_rhr"],
        "平均HRV": r["avg_hrv"],
        "总消耗": f"{r['total_energy']} kcal",
    }, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true",
                        help="以 streamable-http 运行（VPS 上给 Nox Core 调）")
    # 老参数留着不报错，但走的是 streamable-http ——
    # systemd 单元里如果还写着 --sse，服务不会因为参数不认识而起不来
    parser.add_argument("--sse", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.http or args.sse:
        if args.sse:
            print("注意：--sse 已废弃，实际用的是 streamable-http（Core 只支持这个）")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
