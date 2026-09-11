#!/usr/bin/env python3
"""
FSR402 触摸数据接收服务（touch-server）
收 ESP32 发来的 POST /touch，body 为 JSON，逐条追加到 touch_moments.jsonl。

启动：python3 touch_server.py
端口：9333（可用环境变量 TOUCH_PORT 覆盖）
数据：./data/touch_moments.jsonl
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

PORT = int(os.environ.get("TOUCH_PORT", "9333"))
DATA_DIR = os.environ.get("TOUCH_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DATA_FILE = os.path.join(DATA_DIR, "touch_moments.jsonl")

# 可选：给 /touch 加一段暗号做门禁。留空则不加鉴权。
# 设了之后 ESP32 要 POST 到 /touch/<TOKEN>，或用 X-Touch-Token 头。
TOUCH_TOKEN = os.environ.get("TOUCH_TOKEN", "")


class TouchHandler(BaseHTTPRequestHandler):
    # 用 ThreadingHTTPServer：单个断掉的连接不会卡死整个服务
    def do_POST(self):
        # 只认 /touch 路径
        path = self.path.split("?")[0].rstrip("/")
        if not (path == "/touch" or path.startswith("/touch/")):
            self.send_error(404, "not found")
            return

        # 门禁检查
        if TOUCH_TOKEN:
            if path != f"/touch/{TOUCH_TOKEN}" and self.headers.get("X-Touch-Token") != TOUCH_TOKEN:
                self.send_error(403, "forbidden")
                return

        # 读 body
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "bad json")
            return

        # 补时间戳
        body["received_at"] = datetime.now().isoformat(timespec="milliseconds")

        # 写 JSONL
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(body, ensure_ascii=False) + "\n")
        except Exception as e:
            self.send_error(500, f"write failed: {e}")
            return

        # 日志 + 响应（打印除时间戳外的所有字段，兼容新旧两种格式）
        brief = {k: v for k, v in body.items() if k != "received_at"}
        print(f"[{body['received_at']}] {json.dumps(brief, ensure_ascii=False)}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        # 健康检查：不含任何数据，保持公开（探活用）
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if path == "/latest":
            # 🔴 读取也要门禁（2026-09-11 补）。
            # 原来只有 do_POST 查 TOUCH_TOKEN，而 GET /latest **谁都能读** ——
            # 被漏掉的恰恰是隐私那一条：她的身体接触记录。
            # 2026-09-06 日志里已经能看到陌生 IP（193.176.31.253）在敲这个端口。
            # 支持两种带法：`X-Touch-Token` 头，或 `?token=<TOK>` 查询串。
            if TOUCH_TOKEN:
                qtok = ""
                for part in query.split("&"):
                    if part.startswith("token="):
                        qtok = part[6:]
                if (self.headers.get("X-Touch-Token") != TOUCH_TOKEN
                        and qtok != TOUCH_TOKEN):
                    self.send_error(403, "forbidden")
                    return

            lines = []
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-10:]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"lines": lines}, ensure_ascii=False).encode("utf-8"))
            return
        self.send_error(404, "not found")

    def log_message(self, format, *args):
        # 覆盖默认的 stderr 日志，改成带时间戳的简洁格式
        print(f"{datetime.now().strftime('%H:%M:%S')} {self.address_string()} {format % args}")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), TouchHandler)
    print(f"touch-server listening on 0.0.0.0:{PORT}")
    print(f"data -> {DATA_FILE}")
    if TOUCH_TOKEN:
        print(f"auth -> /touch/{TOUCH_TOKEN} (or X-Touch-Token header)")
    else:
        print("auth -> OFF (no token)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()


if __name__ == "__main__":
    main()
