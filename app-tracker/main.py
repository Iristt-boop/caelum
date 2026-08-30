"""App Tracker — MCP SSE + REST"""
import os, json, sqlite3
from datetime import datetime, timezone, timedelta
from mcp.server.fastmcp import FastMCP
import uvicorn

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")
TZ = timezone(timedelta(hours=8))

mcp = FastMCP("app-tracker")
mcp.settings.transport_security.enable_dns_rebinding_protection = False
mcp.settings.streamable_http_path = "/mcp"


def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT NOT NULL, duration TEXT, created_at TEXT NOT NULL
        )""")
        conn.commit()


#: 多久没数据就算「管道断了」。
#:
#: 🔴 手机 App 记录是**每天都该有**的东西 —— 24 小时一条都没有，
#: 那不是"她没玩手机"，那是写入方停了。
STALE_HOURS = 24


def last_record_at():
    """整个库里最后一条的时刻。**判断数据源还活着的唯一依据。**"""
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT created_at FROM tracks ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def staleness():
    """(最后一条时刻, 距今多少小时, 是不是已经断了)。"""
    last = last_record_at()
    if not last:
        return None, None, True
    try:
        age = (datetime.now(TZ) - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return last, None, True
    return last, round(age, 1), age > STALE_HOURS


def today_tracks():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT app, duration, created_at FROM tracks WHERE created_at >= ? ORDER BY created_at DESC LIMIT 50",
            [today],
        ).fetchall()
    return [{"app": r[0], "duration": r[1], "time": r[2]} for r in rows]


# === MCP Tools ===
@mcp.tool()
def get_today_apps() -> str:
    """查询今天使用过的所有 App 和时长"""
    rows = today_tracks()
    if not rows:
        # 🔴 **「今天没记录」和「管道断了」不是一件事。**
        #
        # 这两个长得一模一样，正是它 2026-08-02 停掉之后没人发现的原因 ——
        # 08-02 给 Caddy 路径加了随机前缀防裸奔，手机上的快捷指令还在
        # 往旧地址发，收到的是 404。而这边照常回「今天还没有记录」，
        # 看起来就像她那天没玩手机。**28 天，每天都这么说一遍。**
        last, age, stale = staleness()
        if stale and last:
            return (f"⚠️ 这个数据源可能断了：最后一条是 {last[:16]}，"
                    f"已经 {age:.0f} 小时没有新记录。"
                    f"不要当成「她没用手机」——先去看写入方还活着吗。")
        if stale:
            return "⚠️ 这个数据源一条记录都没有过，写入方可能从没接上。"
        return "今天还没有记录"
    lines = []
    for r in rows:
        dur = f" · {r['duration']}" if r["duration"] else ""
        ts = r["time"].split("T")[1][:5] if "T" in r["time"] else ""
        lines.append(f"{ts} {r['app']}{dur}")
    return "\n".join(lines)


@mcp.tool()
def log_app_usage(app: str, duration: str = "") -> str:
    """记录一个 App 使用事件"""
    ts = datetime.now(TZ).isoformat()
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO tracks (app, duration, created_at) VALUES (?, ?, ?)", [app, duration, ts])
        conn.commit()
    return f"ok: {app}"


# === REST Handlers (raw ASGI) ===
async def post_track(receive, send):
    body = b""
    more = True
    while more:
        msg = await receive()
        if msg["type"] == "http.request":
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
    try: data = json.loads(body)
    except:
        await send({"type":"http.response.start","status":400,"headers":[(b"content-type",b"application/json")]})
        await send({"type":"http.response.body","body":b'{"error":"invalid json"}'})
        return
    ts = datetime.now(TZ).isoformat()
    app_name = data.get("app") or data.get("name") or "Unknown"
    dur = data.get("duration") or ""
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO tracks (app, duration, created_at) VALUES (?, ?, ?)", [app_name, dur, ts])
        conn.commit()
    await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"application/json")]})
    await send({"type":"http.response.body","body":b'{"ok":true}'})


async def get_today(send):
    data = json.dumps(today_tracks(), ensure_ascii=False).encode()
    await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"application/json")]})
    await send({"type":"http.response.body","body":data})


async def get_health(send):
    """🔴 **别再回硬编码的 ok。**

    原来这里永远是 `{"status":"ok"}` —— 于是 systemd 说 active、
    /health 说 ok，而数据源已经死了 28 天。
    进程活着不等于管道活着，健康检查要检查的是后者。
    """
    last, age, stale = staleness()
    body = json.dumps({
        "status": "stale" if stale else "ok",
        "last_record_at": last,
        "age_hours": age,
        "stale_after_hours": STALE_HOURS,
    }, ensure_ascii=False).encode()
    await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"application/json")]})
    await send({"type":"http.response.body","body":body})


# === ASGI 合并 ===
init_db()
mcp_app = mcp.streamable_http_app()


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        await mcp_app(scope, receive, send)
    elif scope["type"] == "http":
        path = scope["path"]
        method = scope["method"]
        if path == "/health" and method == "GET":
            # consume receive first, then send response
            while True:
                msg = await receive()
                if msg["type"] == "http.request" and not msg.get("more_body", False):
                    break
            await get_health(send)
        elif path == "/today" and method == "GET":
            while True:
                msg = await receive()
                if msg["type"] == "http.request" and not msg.get("more_body", False):
                    break
            await get_today(send)
        elif path == "/track" and method == "POST":
            await post_track(receive, send)
        else:
            await mcp_app(scope, receive, send)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
