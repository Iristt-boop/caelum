# ============================================================
# touch-mcp — 共感娃娃触摸记录 MCP 服务
# 把 touch-server 收到的触摸记录（touch_moments.jsonl）包装成
# MCP 工具，让 claude.ai 上的小克能查「她摸了几次、多久、哪几个位置」。
#
# 启动：TOUCH_TRANSPORT=streamable-http python3 server.py
# 端口：9336（TOUCH_PORT 可覆盖）
# 数据：TOUCH_DATA_FILE 指向 touch-server 的 jsonl
# ============================================================

import os
import json
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("TOUCH_PORT", "9336"))
DATA_FILE = os.environ.get(
    "TOUCH_DATA_FILE", "/root/touch-server/data/touch_moments.jsonl"
)

# 位置英文名 → 中文
SENSOR_NAMES = {
    "right_hand": "右手",
    "belly": "肚子",
    "left_hand": "左手",
    "head_right": "头右",
    "back": "背",
}


def _name(sensor: str) -> str:
    return SENSOR_NAMES.get(sensor, sensor)


def _load_press_ends() -> list[dict]:
    """读 jsonl，只返回新格式的 press_end 事件（每条 = 一次完整触摸）。"""
    if not os.path.exists(DATA_FILE):
        return []
    out = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("event") == "press_end":
                out.append(obj)
    return out


def _parse_time(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


mcp = FastMCP("touch-mcp", host="0.0.0.0", port=PORT)


@mcp.tool()
async def get_touch_records(sensor: str = "", limit: int = 20) -> str:
    """查最近的触摸记录。sensor 可传位置英文名(right_hand/belly/left_hand/head_right/back)或中文(右手/肚子/左手/头右/背)，留空查全部。limit 默认 20。返回每次触摸的位置、时长(秒)、峰值、时间。"""
    events = _load_press_ends()
    if not events:
        return "还没有任何触摸记录。"

    # 按位置筛选
    want = sensor.strip()
    if want:
        # 中文 → 英文
        reverse = {v: k for k, v in SENSOR_NAMES.items()}
        key = reverse.get(want, want)
        events = [e for e in events if e.get("sensor") == key]
        if not events:
            return f"没有位置「{_name(key)}」的触摸记录。"

    limit = max(1, min(limit, 100))
    recent = events[-limit:][::-1]  # 最新的在前

    lines = []
    for e in recent:
        t = _parse_time(e.get("received_at", ""))
        tstr = t.strftime("%m-%d %H:%M:%S") if t else "?"
        dur = e.get("duration", 0)
        peak = e.get("peak", 0)
        lines.append(f"{tstr}  {_name(e.get('sensor', '?'))}  摸了 {dur} 秒（峰值 {peak}）")
    return "\n".join(lines)


@mcp.tool()
async def touch_summary(hours: int = 24) -> str:
    """汇总最近 N 小时的触摸：每个位置摸了几次、总时长多少秒、平均每次多久。hours 默认 24。"""
    events = _load_press_ends()
    if not events:
        return "还没有任何触摸记录。"

    cutoff = datetime.now() - timedelta(hours=max(1, hours))
    fresh = []
    for e in events:
        t = _parse_time(e.get("received_at", ""))
        if t and t >= cutoff:
            fresh.append(e)

    if not fresh:
        return f"最近 {hours} 小时内没有触摸记录。"

    by_sensor = {}
    for e in fresh:
        s = e.get("sensor", "?")
        d = float(e.get("duration", 0))
        by_sensor.setdefault(s, {"count": 0, "total": 0.0})
        by_sensor[s]["count"] += 1
        by_sensor[s]["total"] += d

    lines = [f"最近 {hours} 小时，共摸 {len(fresh)} 次："]
    for s in sorted(by_sensor, key=lambda k: -by_sensor[k]["total"]):
        stat = by_sensor[s]
        avg = stat["total"] / stat["count"]
        lines.append(
            f"  {_name(s)}  {stat['count']} 次，共 {stat['total']:.1f} 秒，平均每次 {avg:.1f} 秒"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    _app = mcp.streamable_http_app()
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    print(f"touch-mcp listening on 0.0.0.0:{PORT}")
    print(f"data -> {DATA_FILE}")

    # 部署健康检查（2026-09-11 铺 release 布局时加的）。
    # deploy-remote.sh 要求它返回 200，否则自动回滚。
    #
    # ⚠️ 查的是**数据目录在不在**，不是「服务活着吗」—— 真出问题的地方是
    #    数据路径指到别处，那时服务照常起、照常 200，只有数据不见了。
    #    （特意不查文件本身：数据文件要等她摸过娃娃才有，查它会让全新安装永远不健康。）
    from starlette.responses import JSONResponse

    async def _health(_request):
        d = os.path.dirname(DATA_FILE)
        ok = os.path.isdir(d)
        return JSONResponse(
            {"ok": ok, "data_file": DATA_FILE, "data_dir": d, "data_dir_exists": ok},
            status_code=200 if ok else 503,
        )

    _app.add_route("/health", _health, methods=["GET"])

    uvicorn.run(_app, host="0.0.0.0", port=PORT)
