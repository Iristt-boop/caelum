#!/bin/bash
curl -s "https://restapi.amap.com/v3/geocode/regeo?location=113.598,34.735&key=REDACTED-AMAP-KEY&radius=1000&extensions=all" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('status:', d.get('status'), '|', d.get('info',''))
if d.get('regeocode'):
    print('address:', d['regeocode'].get('formatted_address','')[:80])
"
