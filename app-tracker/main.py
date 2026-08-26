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
    if not rows: return "今天还没有记录"
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
    await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"application/json")]})
    await send({"type":"http.response.body","body":b'{"status":"ok"}'})


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
