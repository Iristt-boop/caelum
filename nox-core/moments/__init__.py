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

from attention.resonance import DRIVE_WORDS as _RESONANCE_WORDS

#: drive 名 → 人话。**只影响怎么说**，一个字节都不参与算分。
#:
#: 这张表原来私有在 `impulse.py` 里（`_WORDS`），T4 把它搬上来变成公共的：
#: `writer.py` 也要用同一张 —— 帖子里的气氛词和 `why` 里的称呼各留一份
#: 必然各自漂移（帖子里说「想她」、日志里说「想老婆」），而测试只盯得住一份。
#:
#: 情绪那部分**直接用情绪层的那张**（`attention.resonance.DRIVE_WORDS`，
#: 2026-09-23 收拢：原来这里抄了一份，加醋意/委屈时没跟上，
#: 写帖子的提示词里会原样漏出 "jealousy"）。不 import 上下文组装层那边 ——
#: 思考层不该伸手进上下文层，两边都往下依赖情绪层。
#: 这里只加 Moments 自己才有的来源（梦）。
DRIVE_WORDS = {
    **_RESONANCE_WORDS,
    # 梦（2026-09-21 她拍板：梦常态发 Moments，他自己发言的地方）
    "dream": "梦里见的",
}
