# health-mcp

个人健康数据管道：iPhone 快捷指令 → VPS → MCP → AI Agent

```
iPhone 快捷指令(每天自动) → POST /health/sync → SQLite → MCP tools → Agent
```

## 组成

| 文件 | 作用 | 端口 |
|------|------|------|
| `server.py` | FastAPI 数据接收服务，快捷指令 POST 到这里 | **8102** |
| `health_mcp.py` | MCP Server，把健康数据暴露成 Agent 工具 | 8101 (SSE) |

> ⚠️ **`server.py` 原来写的是 8100，那是 `nox-core` 的端口**，照抄会撞车。
> 2026-08-02 改成 8102。本机端口占用情况见 PROJECT.md 第四节。

## VPS 部署

```bash
pip install -r requirements.txt

# 必填：快捷指令里要填的鉴权 token。没配会拒绝启动（这个端点在公网上）
export HEALTH_SYNC_TOKEN="随机长字符串"
export HEALTH_DB="/root/data/health.db"      # 和 bridge 的库放一起，备份一次兜住

# 数据接收服务。绑 127.0.0.1，公网那一段交给 Caddy
uvicorn server:app --host 127.0.0.1 --port 8102

# MCP（只给同机的 Nox Core 调，默认也只听 127.0.0.1）
python health_mcp.py --sse
```

> VPS 时区必须是 Asia/Shanghai（`get_today_health` 按服务器本地日期判断「今天」）。
> 当前线上已经是（见 PROJECT.md 第四节）。

### Caddy 反代（线上用的是 Caddy 不是 nginx）

```caddy
noxtang.com {
    handle_path /health/* {
        reverse_proxy localhost:8102
    }
}
```

`health_mcp` 不反代 —— 它没有自己的鉴权层，只给同机的 Core 调，不该出现在公网上。

<details><summary>nginx 参考（历史，线上不用）</summary>

```nginx
# 数据接收
location /health/ {
    proxy_pass http://127.0.0.1:8102;
}

# MCP SSE
location /health-mcp/ {
    proxy_pass http://127.0.0.1:8101/;
    proxy_buffering off;          # SSE 必须关缓冲
    proxy_read_timeout 86400;
}
```

</details>

## MCP 接入

线上由**同机的 Nox Core** 连 `http://127.0.0.1:8101/sse`，不经公网。

⚠️ 想从外部（WorkBuddy / Claude Desktop）连的话，**不能直接把 8101 反代出去** ——
这个 MCP 没有任何鉴权，暴露出去等于把健康数据公开。
真要外部访问，照 `ha-mcp` 的做法在 Caddy 路径里加一段随机 token
（见 PROJECT.md 第五节），或者用 stdio 模式本地起。

## 数据字段说明

`server.py` 同时兼容两种 JSON 字段风格：

| camelCase (快捷指令参考图) | snake_case (后端数据库) | 说明 |
|----------------------------|------------------------|------|
| `activeEnergy` | `active_energy` | 活动能量 kcal |
| `distance` | `distance_km` | 距离 km；快捷指令传米也会自动转 km |
| `avgHeartRate` | `avg_heart_rate` | 平均心率 bpm |
| `minHeartRate` | `min_heart_rate` | 最小心率 bpm |
| `maxHeartRate` | `max_heart_rate` | 最大心率 bpm |
| `restingHeartRate` | `resting_heart_rate` | 静息心率 bpm |
| `hrv` | `hrv_ms` | HRV ms |
| `totalSleep` | `sleep_duration_min` | 总睡眠 分钟 |
| `deepSleep` | `deep_sleep_min` | 深睡 分钟 |
| `remSleep` | `rem_sleep_min` | REM 分钟 |
| `lightSleep` | `core_sleep_min` | 浅睡/核心睡眠 分钟 |
| `awakeSleep` | `awake_min` | 睡眠中清醒 分钟 |
| `sleepSamples` | — | 原始睡眠样本数组，后端自动解析出上面的睡眠分期 |

你不需要改快捷指令里的 JSON 字段名，后端会自动识别。

### 原始睡眠样本格式

如果不想在快捷指令里判断睡眠分期，可以直接传原始样本：

```json
{
  "sleepSamples": [
    {"value": "深睡", "startDate": "2026-07-29T23:00:00+08:00", "endDate": "2026-07-30T00:30:00+08:00"},
    {"value": "REM", "startDate": "2026-07-30T00:30:00+08:00", "endDate": "2026-07-30T01:00:00+08:00"}
  ]
}
```

后端会自动累加各分期时长，并计算总睡眠。

## Agent 可用工具

| 工具 | 说明 |
|------|------|
| `get_today_health` | 今日数据（通常不完整，快捷指令每天只传一次） |
| `get_yesterday_health` | 最近一条完整数据（最常用） |
| `get_latest_sleep` | 最近一晚睡眠分期 |
| `get_health_history` | 最近 N 天趋势 |
| `check_health_warnings` | 异常指标检查（心率/睡眠/HRV/活动量） |
| `get_weekly_summary` | 7 天汇总，适合周复盘 |

## 手机端配置

见 [SHORTCUTS配置.md](./SHORTCUTS配置.md)
