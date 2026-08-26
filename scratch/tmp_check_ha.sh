#!/bin/bash
echo "=== person.nox ==="
curl -s -H 'Authorization: Bearer REDACTED-JWT' http://localhost:8123/api/states/person.nox | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('状态:', d.get('state'))
attrs = d.get('attributes',{})
print('tracker列表:', attrs.get('device_trackers',[]))
print('坐标:', attrs.get('latitude'), attrs.get('longitude'))
"

echo ""
echo "=== device_tracker 实体 ==="
curl -s -H 'Authorization: Bearer REDACTED-JWT' http://localhost:8123/api/states | python3 -c "
import sys,json
for s in json.load(sys.stdin):
    eid = s.get('entity_id','')
    if 'tracker' in eid or 'person' in eid:
        print(eid, s.get('state'), s.get('attributes',{}).get('latitude','-'))
"
