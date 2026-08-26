#!/bin/bash
# 生成 watch token 并追加到 mcp-urls.txt
TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(12))')
echo "watch_token=$TOKEN" >> /root/mcp-urls.txt
echo "watch_token=$TOKEN"
