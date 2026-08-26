#!/bin/bash
echo "=== device_tracker.iris 完整状态 ==="
curl -s -H 'Authorization: Bearer REDACTED-JWT' http://localhost:8123/api/states/device_tracker.iris | python3 -m json.tool

echo ""
echo "=== mobile_app 相关传感器 ==="
curl -s -H 'Authorization: Bearer REDACTED-JWT' http://localhost:8123/api/states | python3 -c "
import sys,json
for s in json.load(sys.stdin):
    eid = s.get('entity_id','')
    if 'iris' in eid or 'mobile_app' in eid:
        print(eid, s.get('state'))
"
