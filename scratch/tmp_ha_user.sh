#!/bin/bash
echo "=== HA 用户 ==="
curl -s -H "Authorization: Bearer ${HA_TOKEN:?请先 export HA_TOKEN}" http://localhost:8123/api/config/auth | python3 -m json.tool
