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
  理解层（她这句话意味着什么）：LLM Appraisal（后台线程，回应后抽取）
    → 事件锚点「她说的：毕设」进 Registry → UnderstandingProvider 回他的上下文
    ⚠️ `NOX_LLM_APPRAISAL` 默认 off，shadow 只记日志不写库
  MemoryProvider（2026-09-05 解禁）：由理解层驱动，两条件命中其一才翻记忆
    ① 有活跃理解锚点 ② 她这句话在指向过去。走 OB 的 touch=False 只读检索
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

存储一览：bridge `/root/data/nox-bridge.db` · nox-core `sessions/attention/world/topics/orders` **五库** · OB `buckets/ + embeddings.db`（全 SQLite；备份见 `scripts/caelum-backup.sh`，体检 `/root/doctor.sh`，探活 `/api/health`）。

> `orders.db`（2026-09-06 加）：待确认单的状态机。不并进 world.db —— 那边契约是
> 「Observation 冻结只追加、永不改写」，订单状态天生要改。付款链接单独一张表带 TTL。

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
| R8 | **花钱的动作模型够不着。** 下单/支付类接口只准挂在确认端点后面（`/api/nox/orders/{id}/confirm`），不许进工具表。工具最多「准备交易」——出一张带快照和指纹的卡，她点了才执行 | 无 | `tests/test_orders.py::test_tool_never_places_the_order` |

> R8 的由来（2026-09-06）：在它之前「确认制」的全部实现是工具描述里那句
> 「她没确认就不许调」，而 `tools/mcd.py` 的 `ACTION_TOOLS` 常量**定义完之后
> 整个仓库没有任何地方用到**，测试也只断言「描述里有『确认』二字」——
> 验的是那句话写了没有，不是确认真的发生了没有。
> ⚠️ 麦当劳目前**还是提示词确认制**，属于已知欠账（见第四节）。

## 三、新能力三问（每加一个能力必须先回答）

1. **它属于哪层？** 感知 / 记忆 / 思考 / 行动 / 表现——写进本图对应层，放不进去的要想清楚是不是该做。
2. **谁消费它？** 答案是「以后可能用」= 现在不做。配上了 ≠ 用上了。
3. **闭环完整吗？** 从触发到她可感知的输出，链条断在哪一段就不算完成（例：共影不是「能放视频」，是 拉流→检测→理解→决定说→记录 全通）。

## 四、已知占位与待接（截至 2026-09-05）

- OS UI：Tasks/Skills/Agents/Workflows 及多数 Settings 子页是占位壳（RoomPlaceholder）；caelum-room（像素房间 MCP）未接入 Room 页
- 手机端 ToolDrawer 三 tab 纯样子；Home widget 墙部分静态
- Resonance 不参与开口决策（V5）；话题池前端页未接（API 已有）
- 理解层（2026-09-05 起）P1+P3 已上线：LLM Appraisal + UnderstandingProvider +
  MemoryProvider 解禁（OB 只读检索）。**理解层默认影子模式**，转正等一周真实日志 ——
  在那之前记忆的加载条件①（活跃锚点）恒为 False，只有条件②（指向过去的说法）在跑。
  待做：关系状态可写可落盘 + 她点头的确认界面（P4）
- 点单确认卡（2026-09-06）P1+P2 已上线（瑞幸）。**欠账**：麦当劳还是提示词确认制，
  没走 R8 的结构闸门；P3 自动查取餐码（轮询 `queryOrderDetailInfo` → CareLedger
  记 `order_update` 不吃配额）还没做，形状已验（`orderStatusName` / `takeMealCodeInfo`）
- 淘宝：**决定不做卡片**（2026-09-06 结案，见 `Caelum-点单确认卡-设计.md` 第十一节）。
  ⚠️ 服务端 instructions 里提到 `buy_now`，但 `tools/list` 里没有 ——
  **不许用调用去探测它在不在**，真存在的话那一下就是真下单
- 废弃物已归档：root `archive/`（memory/、haven-ombre/）、nox-app `archive/`（一代 backend、render/Dockerfile）
- ⏸ **12306 暂缓（2026-09-05 查实）**：第三方包 12306-mcp 的请求被 12306 反爬**无声丢弃**（查票必挂起，60s 无响应；它 fetch 连 UA 都没带，补了 UA 仍挂——缺 cookie 会话流程）。**境外 IP 没被封**：裸 curl 带 cookie 预热+Referer+UA 能查到真实余票。现状：tools/train.py 和桥（mcp-train.service，disabled）都留着；恢复路 = 自写 REST 工具约 80 行（流程已验证），糖糖说想上时再做
- 🔭 **观察点：支付宝 AI 开放平台（aipay.alipay.com，2026-07 上线邀测）**——蜜雪冰城/肯德基/东航等首批以 MCP 插件/Skill 接入「阿宝」，走平台托管不对外发个人 Key。**等它开放个人开发者接入时接一次 = 白得一串茶饮/餐饮品牌**（喜茶/奈雪/茶百道等目前均无独立 MCP）。集成入口：Studio → MCP 面板（/api/nox/integrations）
- 独立小项目（2026-09-05 确认保留，不归档）：`dsh-vscode-layout`（DSH 的 IDE 改造）、`fsr402-*`（传感器硬件实验）、`pixel-beads-generator`（拼豆工具）
