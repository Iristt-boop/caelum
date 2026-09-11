#!/bin/bash
echo "=== zone 实体 ==="
curl -s -H "Authorization: Bearer ${HA_TOKEN:?请先 export HA_TOKEN}" http://localhost:8123/api/states | python3 -c "
import sys,json
for s in json.load(sys.stdin):
    eid = s.get('entity_id','')
    if eid.startswith('zone.'):
        attrs = s.get('attributes',{})
        print(eid, attrs.get('latitude','?'), attrs.get('longitude','?'), '| 半径:', attrs.get('radius','?'))
"
