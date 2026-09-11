#!/bin/bash
# 用当前 GPS 坐标修正 zone.home
curl -s -X POST \
  -H "Authorization: Bearer ${HA_TOKEN:?请先 export HA_TOKEN}" \
  -H 'Content-Type: application/json' \
  http://localhost:8123/api/services/zone/reload

# 更新 zone.home 经纬度
curl -s -X POST \
  -H "Authorization: Bearer ${HA_TOKEN:?请先 export HA_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"latitude": 34.7350, "longitude": 113.5980, "radius": 200}' \
  http://localhost:8123/api/config/core/check_entity
