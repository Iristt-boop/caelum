#!/usr/bin/env python3
"""Ombre-Brain 绑回环（排期 0.6）。在 VPS 上跑：python3 /tmp/ob-bind-loopback.py"""
import sys

P = "/root/ombre-brain/server.py"
OLD = '''# host="0.0.0.0" so Docker container's SSE is externally reachable
# stdio mode ignores host (no network)
mcp = FastMCP(
    "Ombre Brain",
    host="0.0.0.0",'''
NEW = '''# 🔴 绑回环（2026-09-13，排期 0.6）。
# 原来是 host="0.0.0.0"，注释写着「为了 Docker 容器里的 SSE 能被外部访问」——
# 但它早就不在 Docker 里了，是 systemd 直跑；而公网入口一直是 Caddy 反代
# localhost:8002。也就是说 0.0.0.0 这一位从改成 systemd 那天起就没有用处，
# 只留下一个「安全组一旦被改就全裸」的口子。
# 这里装的是她全部的长期记忆，不该靠一层云防火墙当唯一防线。
mcp = FastMCP(
    "Ombre Brain",
    host="127.0.0.1",'''

s = open(P, encoding="utf-8").read()
if 'host="127.0.0.1"' in s:
    print("已经改过了")
    sys.exit(0)
if s.count(OLD) != 1:
    print(f"🔴 锚点出现 {s.count(OLD)} 次，期望 1 次 —— 中止，不猜")
    sys.exit(2)
open(P, "w", encoding="utf-8").write(s.replace(OLD, NEW))
print("✅ 改好了")
