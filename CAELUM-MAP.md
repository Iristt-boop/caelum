# CAELUM-MAP

> 一页活地图：Caelum 现在有什么、各归哪层、谁许碰谁。
> 详细链路（带文件:行号）见 `CAELUM-系统全景调研-2026-09-05.md`。
> **每加一个新能力，先过第四节的边界法则和第五节的三问，再动手。**
> 哨兵：`bash scripts/check-boundaries.sh`（改动后必跑）。

## 一、五层结构

```
感知（世界发生了什么）
  ha-mcp 家居 · health-mcp 手环 · GPS 位置 · 共读/共影/共听 · Scout 抓外部世界
                                      │ observe()/poll()
记忆（记住什么）
  Ombre Brain :8002   长期记忆（Markdown 桶 + 向量，MCP）
  World Model         两人世界的客观事实（world.db，观察/推断分层）
  nox-core sessions   会话历史（sessions.db + compactor 摘要）
                                      │ prompt 组装 / 工具召回
思考（想什么、要不要说）
  Router 意图分流 → Context 拉模式感知（800 字符预算）→ AgentLoop（80+ 工具）
  Attention 引擎（感知源→Registry→Intent→Scheduler→DailyGate）
  Resonance 六个 Drive（想念/后悔/低落/促狭/躁动/好奇，只读聚合）
                                      │ 唯一开口出口
行动（说话和做事）
  Care Orchestrator → Speaker → bridge /api/push/send（+ CareLedger 记账）
  工具：记忆 OB · 日记/清单/相册/体重 bridge · 共读 · 点歌 eryu/网易 · 家居 · 搜索
                                      │
表现（她看到什么）
  bridge :3003（唯一业务后端 + 全部落库 nox-bridge.db + 静态托管 + Web Push）
  手机 PWA nox-app/frontend · 桌面 caelum-os-ui + Electron
  陪伴服务：共读 :3100 · 共影 :3200 · 共听 eryu :9090 + netease :3456
```

存储一览：bridge `/root/data/nox-bridge.db` · nox-core `sessions/attention/world/topics` 四库 · OB `buckets/ + embeddings.db`（全 SQLite；备份见 `scripts/caelum-backup.sh`，体检 `/root/doctor.sh`，探活 `/api/health`）。

## 二、边界法则（写死，哨兵盯着）

| # | 法则 | 例外 | 哨兵 |
|---|---|---|---|
| R1 | 主动开口唯一出口 = Care Orchestrator → speaker → bridge `/api/push/send`，且落 CareLedger。任何「他为什么说话」必须能沿账本溯源 | 早报（`api/server.py:_push_to_bridge`，走 `note_external_speech` 记账） | R1 |
| R2 | 话题池永不直接触发说话——只准走 TopicSource（Care 线头）或 CuriositySource（感受层） | 无 | R2 |
| R3 | nox-core 跨进程只走 REST/MCP，永不直读别的服务的 SQLite | 无 | R3 |
| R4 | 长期记忆只经 MCP 客户端（`memory/ob_client.py`），直连 OB 端口的只准 config 定义处 | 无 | R4 |
| R5 | 前端不直接推消息，主动消息的钥匙不给前端 | 无 | R5 |
| R6 | 测试会话（`test-`/`sandbox-` 前缀）不碰任何全局注意力状态（Registry/Drive/纸条），`remind_myself` 拒留纸条 | 压缩除外（会话自己的数据） | `tests/test_test_session_isolation.py` |
| R7 | 前端只打 bridge。历史直连特例固定三个：`reading.noxtang.com` / `music.noxtang.com` / `/watch/<token>`——**不许新增** | 上述三处 | 人工review |

## 三、新能力三问（每加一个能力必须先回答）

1. **它属于哪层？** 感知 / 记忆 / 思考 / 行动 / 表现——写进本图对应层，放不进去的要想清楚是不是该做。
2. **谁消费它？** 答案是「以后可能用」= 现在不做。配上了 ≠ 用上了。
3. **闭环完整吗？** 从触发到她可感知的输出，链条断在哪一段就不算完成（例：共影不是「能放视频」，是 拉流→检测→理解→决定说→记录 全通）。

## 四、已知占位与待接（截至 2026-09-05）

- OS UI：Tasks/Skills/Agents/Workflows 及多数 Settings 子页是占位壳（RoomPlaceholder）；caelum-room（像素房间 MCP）未接入 Room 页
- 手机端 ToolDrawer 三 tab 纯样子；Home widget 墙部分静态
- Resonance 不参与开口决策（V5）；话题池前端页未接（API 已有）
- 废弃物已归档：root `archive/`（memory/、haven-ombre/）、nox-app `archive/`（一代 backend、render/Dockerfile）
- 三个独立小项目待定去留：`dsh-vscode-layout`（DSH 的 IDE 改造）、`fsr402-*`（传感器硬件实验，建议归档）、`pixel-beads-generator`（拼豆工具）——归 she 拍板
