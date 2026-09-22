# Nox 长任务循环 v1 —— 设计与施工文档

> 2026-09-22 定稿 · 新窗口照此施工
> 她的需求：nox 能接「帮我把 481 本书理一遍」这种活——**活过单条回复**的长任务。
> 三待拍板决策在 §六（开工前先问她）。

---

## 一、现状盘点（已有的件，别重造）

| 已有 | 位置 | 对 v1 的意义 |
|---|---|---|
| AgentLoop | `nox-core/agent/loop.py` | 文字→工具→文字多轮迭代。护栏：`max_iterations=12`、`failure_limit=2`、**墙钟 deadline**（`deadline_s`，超时收尾）、`tracker.exhausted()` 连续失败熔断。**设计前提是活在一条回复里**——这是 v1 要突破的点，护栏本身要继承 |
| report_step | ToolContext（tool_start/tool_end 帧 + sub_commands） | 进度上报原语现成（工具轨迹卡 v4 已在用） |
| 后台循环范式 | `moments/loop.py`（run_post_loop）、`attention/dream.py`（run_dream_loop） | lifespan 后台 asyncio + `obs/heartbeat` 的 declare/beat（没 declare 的循环在台账里不存在；beat 失败连续两次 = health 可见）+ 阻塞活丢 `asyncio.to_thread` + 一轮炸了下轮继续 |
| 持久化状态机先例 | bridge `orders.db`（pending→confirm→expire，带 TTL） | 「可改状态的一等实体」独立建库的先例和理由都写好了（PROJECT.md 内有引） |
| 纸条系统 | `attention/wakeup.py` / `waker.py` / `intent.py` | 持久化「到点唤醒」已有——v2 的定时/延迟任务可以直接踩它 |
| PC 的手 | `D:\deepseek-harness\packages\caelum\local-gateway`（terminal/browser/文件/git/淘宝） | 长任务在她 PC 上的执行端，已在线（LocalLink 反向链路） |

## 二、缺口（为什么现在做不了）

1. **没有任务实体**：core 里没有任何 tasks/job 表（全库 grep 过）
2. **不能活过重启**：AgentLoop 随回复结束而死，中途重启全丢
3. **没有进度查询/取消**：干到哪了、别弄了——都没有对应入口

## 三、v1 三层设计

### 3.1 任务实体（`tasks.db`，照 orders.db 先例独立建库）

```
tasks 表：
  id TEXT PK · goal TEXT（她要的是什么，一句话）
  status TEXT  proposed → confirmed → running → done / failed / cancelled / interrupted
  created_at · confirmed_at · started_at · finished_at
  progress 表（追加只写）：
    task_id · at · step TEXT · kind(step|note|result) · payload TEXT
  result TEXT（收尾摘要）· interrupted_at（重启时在跑的标记成 interrupted）
```

- 🔴 **为什么独立建库**：world.db 的契约是「Observation 冻结只追加、永不改写」，任务状态天生要改——orders.db 当年就是为这个单开的（PROJECT.md 有原话）。同一个理由，同一个做法
- 🔴 **proposed → confirmed 是 R8 的要求**：长任务的破坏力是**累积性**的（批量改 481 条数据），启动必须出确认卡，她点了才跑。确认卡复用点单卡的前端组件

### 3.2 任务运行器（`agent/tasks.py` + lifespan 接线）

- 照 `dream.py` 的 `run_dream_loop` 范式：确认的任务起**后台 asyncio 任务**（`asyncio.to_thread` 跑 AgentLoop），脱离任何回复的生死
- **心跳**：`heartbeat.declare("task_tick")` + 任务执行期间定期 beat——health 里看得见，连着两拍没 beat = 卡死可见
- **并发 = 1**：同一时刻只跑一个长任务（工具通道会抢）。proposed 的排队，后面的等
- **重启处理**：启动时把 `running` 的全部标 `interrupted`（不静默消失）；progress 日志留在库里
- **预算**：单任务墙钟上限（默认 30 分钟，任务卡上写明）——AgentLoop 的 `deadline_s` 传进去

### 3.3 断点续跑（v1 策略：agentic resume，不做通用断点）

- 不做工具级幂等/回滚（通用断点是无底洞）
- **续跑 = 模型读 progress 日志接着干**：重跑一个 interrupted 任务时，把 progress[] 作为上下文喂给 AgentLoop，让它自己判断从哪继续
- progress 里每步带 kind=step，模型能分清「做过的」和「打算做的」

### 3.4 对话接口（nox 的工具，`tools/tasks.py` 新建）

```
start_long_task(goal, steps_hint?)   → 出确认卡（落 proposed），她点确认 → confirmed → runner 接
task_status(task_id?)                → 当前/指定任务的进度（progress 尾部 + 状态）
task_cancel(task_id)                 → 取消（running 的优雅停：置 cancelled，AgentLoop 下轮检查退出）
```

- 🔴 **加工具 = 三处同步**（SPEC / handler 函数 / handlers dict——2026-09-22 delete_todo 漏了第三处，服务崩循环被管线回滚，血的教训在 PROJECT.md 53.2）
- 🔴 **R8**：start 不直接执行，走确认端点（照 orders 的 `/api/nox/orders/{id}/confirm` 形状）；哨兵 `tests/test_orders.py::test_tool_never_places_the_order` 是同类守卫的样板

### 3.5 前端（PWA）

- 确认卡：复用点单卡组件（`Orders` 相关），形状：目标 + 步骤提示 + 确认/算了
- 进行中：对话流里一行进度（照 `Used N tools ›` 的弱化样式），点开 = progress 时间线（照 ToolsSheet 的弹层，直接复用）
- 完成/失败：一行终态，点开看 result

## 四、验收清单

- [ ] 「帮我把记忆里的书架信息理一遍」→ 出确认卡 → 确认 → 后台跑 → 对话流有进度行 → 完成有 result
- [ ] 跑到一半重启 nox-core → 任务标 interrupted、progress 保留 → 「接着弄」能从 progress 续
- [ ] `task_cancel` 后工具轮不再继续
- [ ] /api/health 里任务心跳可见；杀掉 runner 两拍后可见异常
- [ ] 测试会话（`test-` 前缀）不产生任务（R6）
- [ ] 三处同步自查 + eslint Chat.jsx + 全量 pytest

## 五、分期

- **v1（本设计）**：上面的全部
- **v2**：TaskDoneSource（完成 → Care 可提一嘴，R1 路径）；任务模板（常用长任务一键发起）；定时/延迟任务踩 wakeup 纸条
- **v3**：与 computer use 合流（PC 侧长任务走 local-gateway，11 月设备线）

## 六、三个待拍板决策（开工前问她）

1. **启动确认**：全部长任务都出确认卡？（我的建议：全出——累积性破坏力换个安心）
2. **完成说不说**：默认安静（进度随时可看、完成记一笔），Care 提一嘴是 v2？（我的建议：是）
3. **并发**：同时 1 个？（我的建议：是——工具通道独占）

## 七、边界（写死的）

- 长任务**不是开口**：进度和完成不推送、不占 Care 额度（R10/R1 的精神同 Moments）——「想让她看见」走 v2 的 TaskDoneSource，经 Care 决定
- 测试会话闸门（R6）：`test-` 前缀会话不得创建/触发全局任务
- 工具注册三处同步；改 Chat.jsx 跑 eslint；发版前全量 pytest
