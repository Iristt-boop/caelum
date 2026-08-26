#!/bin/bash
echo "=== HA 用户 ==="
curl -s -H 'Authorization: Bearer REDACTED-JWT' http://localhost:8123/api/config/auth | python3 -m json.tool
