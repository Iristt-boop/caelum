"""Moments —— Nox 的自我表达层（v1）。

见 `Caelum-Moments-设计.md`。这一层回答的是
「他怎么会在某个时刻想留一条痕迹」，
不是「他要说什么」（那是 writer），也不是「什么时候轮到他」
（那是 loop 的 post_tick）。

    impulse.py  纯函数：drives + 互动量 + 距上次发帖 → (冲动值, why)
    record.py   T3 —— 一次判断的三段式记录，shadow 的出口
    writer.py   T4 —— 生成正文（utility 模型）
    loop.py     T5 —— 心跳 + 每日上限 + 间隔 + 随机

## 为什么它得是单独一层（不是「日记多写一条」）

系统里**已经有一整层「够不着开口阈值」的内心状态**，每一条都是**故意**
压低的：`attention/playfulness.py:24`「她心情好不该换来一次开口」、
`attention/regret.py:31`「故意压在开口阈值之下」、
`attention/sources/curiosity.py:36` ≤0.45「他对一篇论文好奇，不该变成一次打扰」。

每一条被压低的理由都一样：**不该变成一次打扰她**。

Moments 接的就是掉在阈值底下的那些 —— 所以这里算的是
**「要不要留一条痕迹」**，不是「要不要开口」。开口要过 0.55、
要抢每日配额、要吵醒她；留一条痕迹不用。

## 🔴 两条边界（设计文档第一节）

  1. **发帖不推送。** 产出路径里出现 push/send，就等于把它变成第五条
     主动消息渠道（糖糖 2026-08-18：「不要成为第五条主动消息渠道」）。
     这条不靠自觉 —— R10 哨兵盯着。发帖不走 `Orchestrator._deliver`。
  2. **drives 只读。** 从现成的 `attention.drives()` 拿，绝不回写 Registry
     （`attention/resonance.py` 的三条边界）。
"""

#: drive 名 → 人话。**只影响怎么说**，一个字节都不参与算分。
#:
#: 这张表原来私有在 `impulse.py` 里（`_WORDS`），T4 把它搬上来变成公共的：
#: `writer.py` 也要用同一张 —— 帖子里的气氛词和 `why` 里的称呼各留一份
#: 必然各自漂移（帖子里说「想她」、日志里说「想老婆」），而测试只盯得住一份。
#:
#: 和 `context/providers/resonance.py` 的 `_WORDS` 是同一批词，
#: 但故意不 import 那边 —— 思考层不该伸手进上下文组装层拿一张词表。
#: 认不出来的 drive **原样打印**：以后加新 drive 不用回来改这里，
#: 也不会因为漏了一条就拼出半句话。
DRIVE_WORDS = {
    "longing": "想她",
    "playfulness": "想逗她",
    "regret": "过意不去",
    "dejection": "提不起劲",
    "concern": "担心她",
    "curiosity": "被一件事勾着",
}
