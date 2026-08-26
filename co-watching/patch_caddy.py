#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 Caddyfile 的默认 handle 前插入 co-watching 转发块。

⚠️ **token 不写在这个文件里。** 它是 `/watch/<token>/` 这条路径的全部鉴权 ——
写死在源码里等于把钥匙留在门口（`.env.local` 那份特意注明了「不进仓库」，
这份 2026-08-22 之前漏了）。

按这个顺序找：
    1. 命令行参数：  ./patch_caddy.py <token>
    2. 环境变量：    WATCH_TOKEN=xxx ./patch_caddy.py
    3. VPS 上的账本：/root/mcp-urls.txt 里最后一行 watch_token=xxx
                     （`gen_token.sh` 生成时就是往那儿追加的）

一个都找不到就退出，**不自己编一个** —— 编出来的 token 会让 Caddy 起一条
谁也进不去的路由，而且看不出错。
"""
import os
import re
import subprocess
import sys

CADDYFILE = "/etc/caddy/Caddyfile"
LEDGER = "/root/mcp-urls.txt"
PORT = 3200


def find_token() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    env = os.environ.get("WATCH_TOKEN", "").strip()
    if env:
        return env
    if os.path.exists(LEDGER):
        # 取最后一条 —— 重新生成过的话，新的在后面
        found = re.findall(r"^watch_token=(\S+)", open(LEDGER, encoding="utf-8").read(),
                           re.MULTILINE)
        if found:
            return found[-1]
    print(f"FAIL: 没找到 token。传参、设 WATCH_TOKEN、或先跑 gen_token.sh 写进 {LEDGER}")
    raise SystemExit(1)


token = find_token()
block = f"""    handle_path /watch/{token}/* {{
        reverse_proxy localhost:{PORT}
    }}
"""

text = open(CADDYFILE, encoding="utf-8").read()
if f"/watch/{token}" in text:
    print("已存在，跳过")
else:
    # 在 noxtang.com 站点块里、默认 handle { 之前插入
    marker = "    handle {\n        reverse_proxy localhost:3003\n    }"
    if marker not in text:
        print("FAIL: 找不到默认 handle 块")
        raise SystemExit(1)
    text = text.replace(marker, block + marker, 1)
    open(CADDYFILE, "w", encoding="utf-8").write(text)
    print("已插入 watch 转发块")

# 语法校验 + 重载
r = subprocess.run(["caddy", "validate", "--config", CADDYFILE], capture_output=True, text=True)
print("validate:", r.stdout.strip()[-200:] if r.stdout else r.stderr.strip()[-200:])
if r.returncode == 0:
    subprocess.run(["systemctl", "reload", "caddy"])
    print("caddy 已重载")
