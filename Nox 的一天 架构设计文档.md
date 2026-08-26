文档版本：v1.1

所属系统：Caelum

日期：2026‑08‑18

## 1 概述

### 1.1 定义

**「Nox 的一天」不是独立业务模块，也不是 CareLedger 的简单前端展示。**

它是 **Caelum 中 Nox 当日活动的聚合视图**，将对话、Care、Attention、WakeBook、Task、World、Music、Memory 等已有模块的原始数据，按时间维度重组生成时间线。

> 本质：**Nox 当日状态、行为与陪伴经历的时间投影（Read Model / Projection 只读投影模型）**。

UI 视觉沿用现有设计稿：`00:00 → 24:00` 横轴时间轴，展示「夜间巡查、早安问候、陪你写代码、一起听音乐」等事件节点。

### 1.2 核心原则

1. 不新建独立业务数据表，不重复存储业务事实；原始数据来源为各个已有业务存储；
2. 后端做聚合、过滤、格式化，前端只负责渲染时间线视图；
3. 第一版完全基于真实原始数据聚合，每一条展示事件都可回溯原始数据源；暂不引入复杂 AI 总结；
4. 过滤底层系统心跳、高频工具调用等调试日志，只输出**有业务意义的用户可感知事件**。

### 1.3 整体数据流架构

text

```
                    ┌────────────────────┐
                    │   Caelum Frontend  │
                    │                    │
                    │    Nox 的一天      │
                    └─────────┬──────────┘
                              │
                         GET /api/nox/day
                              │
                    ┌─────────▼──────────┐
                    │   Day Aggregator   │
                    │   「一天聚合器」    │
                    └─────────┬──────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ↓                   ↓                   ↓
     CareLedger          Conversation        World Model
     惦记/主动关心          对话事件             世界状态
          │                   │                   │
          ↓                   ↓                   ↓
      WakeBook             Tasks              Music
      唤醒链              任务/计划            共听
          │                   │                   │
          └───────────────────┼───────────────────┘
                              ↓
                       DayEvent[]
                              ↓
                       Frontend Timeline
```

> 分层处理链路：原始 Raw Events → Event Normalizer（标准化） → Meaning Builder（意义化过滤） → Meaningful Events → DayEvent [] 返回前端

事件三层模型：

1. **Layer1 事实**：底层原始记录（用户发消息、Nox 回复、状态变更）
2. **Layer2 行为**：提炼出 Nox 做了什么行为（Nox 发起早安问候）
3. **Layer3 体验**：最终给到前端展示的用户可读文案（标题 + 摘要）

## 2 核心数据结构 DayEvent

typescript

运行

```
type DayEvent = {
  id: string

  timestamp: string // ISO8601 带时区时间

  type:
    | "care"
    | "conversation"
    | "task"
    | "wake"
    | "attention"
    | "world"
    | "music"
    | "memory"

  source: string // 来源模块标识

  title: string // 前端展示主标题
  summary?: string // 简短描述

  icon?: string // 前端图标标识

  status?:
    | "completed"
    | "active"
    | "skipped"
    | "blocked"

  metadata?: Record<string, unknown>

  related?: {
    conversationId?: string
    careId?: string
    taskId?: string
    memoryId?: string
  }
}
```

### 示例 1：Care 早安事件

json

```
{
  "id": "care_102352",
  "timestamp": "2026-08-18T10:52:00+08:00",
  "type": "care",
  "source": "care_orchestrator",
  "title": "回应你的早安",
  "summary": "和你说了早安",
  "status": "completed",
  "related": {
    "conversationId": "conv_xxx",
    "careId": "care_xxx"
  }
}
```

### 示例 2：Music 共听音乐事件

json

```
{
  "id": "music_204500",
  "timestamp": "2026-08-18T20:45:00+08:00",
  "type": "music",
  "source": "music",
  "title": "和你一起听音乐",
  "summary": "Ocean Eyes",
  "status": "completed"
}
```

## 3 API 接口设计

### 获取 Nox 某一天时间线

http

```
GET /api/nox/day?date=2026-08-18
```

Query 参数

表格

|参数|类型|必填|说明|
|---|---|---|---|
|date|string|是|日期 `yyyy‑MM‑dd`|

响应示例

json

```
{
  "date": "2026-08-18",
  "timezone": "Asia/Shanghai",

  "summary": {
    "activeHours": 14.6,
    "careConsidered": 8,
    "careSpoke": 3,
    "conversations": 6,
    "tasksCompleted": 2
  },

  "events": [
    {
      "id": "xxx",
      "timestamp": "2026-08-18T02:13:00+08:00",
      "type": "attention",
      "title": "夜间巡查",
      "summary": "一切正常"
    },
    {
      "id": "xxx",
      "timestamp": "2026-08-18T09:30:00+08:00",
      "type": "care",
      "title": "早安问候",
      "summary": "和你说了早安"
    },
    {
      "id": "xxx",
      "timestamp": "2026-08-18T14:20:00+08:00",
      "type": "conversation",
      "title": "陪你写代码",
      "summary": "帮你调试毕业设计项目"
    },
    {
      "id": "xxx",
      "timestamp": "2026-08-18T20:00:00+08:00",
      "type": "music",
      "title": "一起听音乐",
      "summary": "Ocean Eyes"
    }
  ]
}
```

> 前端调用示例

tsx

```
<NoxDayTimeline events={data.events} />
```

## 4 后端约束说明

1. ❌ **禁止新建专门存储 Nox 一天的数据表（nox_day）**，避免数据重复存储；
2. 数据源全部来自已有业务：`Conversation`、`CareLedger`、`WakeBook`、`Attention`、`Task`、`World Model`、`Music`、`Memory`；
3. Day Aggregator 职责：拉取多源原始数据 → 标准化归一（Event Normalizer）→ 过滤、去重、排序、意义化加工（Meaning Builder）→ 输出`DayEvent[]`；
4. 过滤底层高频内部心跳、工具调用、上下文刷新等调试类原始事件，只产出用户可感知的有意义事件；
5. **CareLedger 作为一等数据源**，care 的多种状态 `spoke / skip / blocked` 全部纳入聚合；
6. 支持事件点击展开详情，可溯源到原始业务记录（careId /conversationId/taskId）。

### CareLedger 数据源产出示例

原始聚合可以拿到全部状态：

plaintext

```
09:30  Care → spoke
11:15  Care → skip
13:40  Care → blocked
15:02  Care → spoke
16:54  Care → spoke
```

前端默认只展示有体验意义节点，点击节点展开完整详情：

plaintext

```
16:54
惦记你

触发来源
惦记引擎

当时状态
在线 / 空闲

Attention
毕业设计压力

决策
SPEAK

最终消息
「今天写得怎么样了？」

[查看对话]
```

## 5 前端组件拆分设计

plaintext

```
NoxDay
│
├── DayHeader
│   ├── 今天/日期标题
│   └── 「Nox 今天陪了你多久」总览信息
│
├── DayTimeline
│   │
│   ├── TimeAxis
│   │   00 04 08 12 16 20 24 横轴时间刻度
│   │
│   ├── ActivityTrack
│   │
│   └── EventCards
│       ├── CareEvent
│       ├── ConversationEvent
│       ├── MusicEvent
│       ├── TaskEvent
│       └── AttentionEvent
│
└── DaySummary
    ├── 惦记 8 次
    ├── 主动说话 3 次
    ├── 对话 6 次
    └── 一起度过 4.5h
```

### EventCards 事件卡片设计

整体布局统一，通过图标、颜色、微动画区分事件类型：

**Care**

> 09:30
> 
> **早安问候**
> 
> 和你说了早安

**Music**

> 20:00
> 
> **一起听音乐**
> 
> Ocean Eyes

**Task**

> 14:20
> 
> **陪你写代码**
> 
> 毕业设计项目

**Attention**

> 02:00
> 
> **夜间巡查**
> 
> 一切正常

## 6 迭代规划

1. **V1 版本**：纯原始数据聚合，只做过滤、归一、格式化，无 AI 生成总结；每条事件可溯源原始业务 ID；完成时间轴 UI、卡片、汇总统计；
2. **后续迭代**：在现有 Read Model 基础上叠加 AI 能力，例如「今天 Nox 最在意什么」「今日经历总结」，不推翻底层聚合架构。

## 7 关键要点复盘

- Nox 的一天 = **只读投影视图，不是独立业务模块，不新增事实存储**；
- 后端负责多源拉取、过滤、意义化、输出`DayEvent[]`；前端只做渲染；
- CareLedger 是核心一等数据源，完整承载惦记的各种决策状态；
- 区分底层系统日志与用户体验事件，避免时间线变成监控日志；
- 所有展示事件可追溯原始记录，为后续 AI 总结打下基础。