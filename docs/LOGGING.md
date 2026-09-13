# LOGGING — Caelum 日志规范（2026-09-05）

> 这份规范不是给「将来」立的，是给**已经发生过**的事故立的。
> 这个系统的头号敌人是**静默失败**——服务照常、回答照常，只有真相没了。

## 反例墙（每条都是真实事故）

| 事故 | 静默的方式 | 后果 |
|---|---|---|
| usage_log 断更（2026-07-28） | 删 apiMode 时把写入一起删了 | 用量统计冻住，几周后发现 |
| dejection 永远为 0 | 写完了没人喂失败事件 | 一种情绪根本不存在 |
| Care 快循环死亡 | 无报错 | 唯一症状：「他忽然不再想起她」 |
| 共影无声（2026-08-21） | 冒烟测试只验「有字节」 | 无声一天后才发现 |
| DeepSeek 视觉返回空 | reasoning 吃光 max_tokens | 他「装作看见了」 |
| 纸条建好就被撤（2026-08-11） | 日志里看着挺正常 | 整条唤醒链静默失效 |
| Registry 被测试污染（2026-08-24） | 测试流量没有隔离 | 他可能问她没说过的事 |

## 规则

1. **每个 except/catch 必须留痕**。`catch {}` 只准用于「失败有等价默认值」的解析/尽力而为场景（如 `JSON.parse` 用户输入），且该处必须有注释说明吞的是什么。
2. **迁移类失败只许放过「重复执行」**：bridge 用 `dbTry(sql)`——只放行 duplicate column，其余失败打 WARN 再继续启动。
3. **崩溃必须出现在 journalctl**：bridge 挂了 `unhandledRejection` / `uncaughtException` / Express 错误中间件三层兜底（2026-09-05 加），谁删谁负责。
4. **分级**：`ERROR`=需要人干预（服务起不来、数据写坏）；`WARN`=降级但活着（迁移跳过、清理失败、一条推送没送出去）；`INFO`=决策与转折（事件进入 Attention、纸条建/撤、开口与被拦）。规则：**看到日志的人要能回答「接下来该做什么」**，答不出的别打。
5. **日志里要有上下文**：`console.warn("迁移失败:", sql, e.message)` 而不是 `console.warn("error")`。哪张表、哪个会话、哪个桶，写清楚。
6. **「配上了 ≠ 用上了」的验证靠日志**：新能力上线后第一天，去 journalctl 里确认它的 INFO 行真的在出现。没出现 = 没接上。

## 查「他今天怪怪的」从哪开始（2026-09-13 加，审计 1.2/1.3）

**先看 turn 日志。** `nox-core` 每答完一轮落一行，`journalctl -u nox-core | grep 'turn |'`：

```
turn | outcome=tool_stuck iter=2 t=8.4s model=openrouter/sonnet-4.5 stream=0
       tools=breath,hass_set_light✗,hass_set_light✗ tok=in8500/out130/cr12000/cw0
       | 这些工具连续失败 2 次，已停止重试：hass_set_light
```

四组字段各回答一个问题：**跑完了吗**（outcome/iter）· **慢在哪**（t 对 iter：
轮数多还是单轮慢）· **干了什么**（tools，按顺序，失败打 `✗`）· **花了多少**（tok）。

- `outcome=answered` 是 INFO，**其余一律 WARNING** —— 其余每一种她收到的都是不完整的东西
- `outcome=abandoned` = 调用方没取到 done 就把流丢了（她关页面 / SSE 断线 / 上游异常）
- 一轮没有 turn 日志 = 根本没进 loop，去查上游（路由、鉴权、Provider）

**再看 Attention。** `grep 'Attention '`：`决定` = 他记下了 · `松开` = 他放下了 ·
`不关心` = **他为什么没开口**。

**要更细就调级别，不用改代码：** `NOX_LOG_LEVEL=DEBUG`（写进 `/etc/nox/nox-core.env` 后重启）。
DEBUG 多出来的主要是"她说了句普通的话"这类常态忽略 —— 平时没必要开，
查"他怎么对某句话完全没反应"时开。
⚠️ 写错了不拦启动，退回 INFO 并 warning 一句（`config.logging_level()`）。
⚠️ uvicorn 自己那份日志**故意不跟着降**，否则一条 SSE 能刷几百行把上面这些埋掉。

## 各服务日志在哪

- VPS 全部走 systemd → `journalctl -u <服务名>`（bridge / nox-core / ombre-brain / co-reading / co-watching / eryu / netease-mcp）
- 一键体检：`/root/doctor.sh`；全家桶探活：`https://noxtang.com/api/health`
- nox-core 的风格是「增强路径炸了也不影响对话主链 + logger.exception 留痕」——这是对的，保持。

## 已知的良性静默（不用修，但要记得它们存在）

- `nox-core/attention/restlessness.py:178`：数值解析失败回退（TypeError/ValueError → pass）
- `nox-core/context/providers/location.py:436`：定位 Provider 的个别字段解析 pass
- bridge 里 `JSON.parse` / ws.send / unlink 的尽力而为 catch（都有默认值或下次重试）
