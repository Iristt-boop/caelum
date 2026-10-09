# Caelum 文档索引（2026-10-06 整理）

根目录只放入口和**正在写的**稿子；写完、定稿的挪到这里。代码注释里引的是文件名，挪了照样 grep 得到。

## 根目录（入口）

| 文件 | 是什么 |
|---|---|
| `CLAUDE.md` | 小克的规矩（每次对话先读） |
| `PROJECT.md` | 项目全貌 + 变更记录 |
| `CAELUM-MAP.md` | 一页活地图：五层结构 + 边界法则 + 验证纪律 |
| `CAELUM-系统全景调研-2026-09-05.md` | 系统现状全景（带文件:行号） |
| `CAELUM-修复排期.md` | 修复排期 |
| `LOCAL-SECRETS.md` | 本机密钥在哪（不进 git） |
| 其余 `*-设计稿-2026-10-0x.md` | Guide / 配置层 / 沙箱卡片 / OS 语音 / 使用手册 / music-mcp / Aru —— 还在写，写完再挪进 design/ |

## docs/design/ 设计稿

| 文件 | 是什么 |
|---|---|
| `CAELUM-ARCHITECTURE.md` | 总架构 |
| `CAELUM-HARNESS-ARCHITECTURE.md` | Harness（工具 / 授权 / 运行时） |
| `CAELUM-OS-ARCHITECTURE.md` | Caelum OS 桌面端 |
| `CAELUM-RESONANCE-ARCHITECTURE.md` | 情绪系统 Resonance（V1~V5 演进路线） |
| `CAELUM-Temporal-Intent-Contract.md` | 时间理解的契约 |
| `Nox 的一天 架构设计文档.md` | 「他的一天」聚合 |
| `Caelum-Moments-设计.md` | 朋友圈 |
| `Caelum-点单确认卡-设计.md` | 点单确认卡 |
| `Caelum-关系状态-设计稿-2026-10-06.md` | 关系状态（约定 / 别问 / 上心 / 气氛）+ Between Us 页（Memory 菜单下）|
| `Caelum-V5-他自己的时间-设计稿-2026-10-06.md` | V5：他自己的时间 + His Day |

`Nox-长任务循环-v1-设计.md` 还在根目录：有别的会话没提交的改动，等它收尾再挪。

## docs/research/ 调研与审计

| 文件 | 是什么 |
|---|---|
| `CAELUM-架构审计-2026-09-10.md` | 架构审计（记忆污染 / 关系状态缺失等） |
| `CAELUM-时间模型审计-2026-09-14.md` | 时间模型审计 |
| `CAELUM-生态调研-对比与借鉴-2026-09-15.md` | 同类项目对比 |
| `Caelum-AI支付-可行性调研.md` | AI 支付可行性 |
| `CAELUM-Nox搬家调研-VPS转N150-2026-09-21.md` | VPS 搬到 N150 |
| `Caelum-记忆召回调研-修订版-2026-09-21.md` | 记忆召回失效调研：真问题在排序不在召回（带离线复现脚本；⚠️ 标的是未验证推测） |
| `Caelum-记忆召回-两天调研结论-2026-09-22.md` | 两天结论：所有数字建立在她抽检通过的尺子上，09-21 的弱标注数字全部作废（抽检 0/20） |
| `Caelum-rerank上线方案-2026-09-22.md` | rerank 上线方案（她选 A、门槛 0.14）。**已执行**：线上 rerank 10-06 起 on；留着是为了记「为什么」 |

## docs/ 其他

`LOGGING.md`（写代码前读）、`Nox设备系统-完整文档.md`、`RESTART-STATE.md`、`舵机固件施工图.md`

## archive/

`handoff/`（08-08 / 09-13 / 09-22 的交接记录）、`haven-ombre/`、`memory/`、`vps-scripts-legacy-2026-09/`
