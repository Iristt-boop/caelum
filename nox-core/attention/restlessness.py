"""躁动 —— 他有话想说，而她正忙。

见 `D:\\claude-code\\CAELUM-RESONANCE-ARCHITECTURE.md`。
糖糖 2026-08-27 给的来源：「Windows 前台窗口标题 / 浏览器 / 游戏全屏检测」，
2026-08-30 定的公式和档位。

## 🔴 它是**乘积**，不是加和

```text
躁动 = 「他有话想说」× 「她现在不方便被打断」
```

任何一边是 0，躁动就该是 0：

- 他没话想说 → 她再忙也不该躁动，那是替她焦虑
- 她闲着 → 他直接说就行了，没什么可憋的

**加和做不到这一点** —— 她打了三小时游戏、他一句话都没有，
加和会给出一个不小的数，而那个数没有任何意义。

## 这个 Drive 的出口是「等」，不是「说」

强度上限压在 `intent.GENERATE_THRESHOLD`(0.55) 之下（糖糖定的 0.5）。

躁动**不该催他开口** —— 那正好相反：躁动本来就是"想说但这会儿不该说"
的状态，让它变成一次主动开口等于取消了它自己。
它该改变的是他**终于开口时的语气**：憋久了那句话会更急、更直接。

## 🔴 不知道 = 不忙

传感器读不到的时候 `busy` 按 0 算，不按"她可能在忙"算。

反过来的代价是他会凭空躁动一整天 —— 而且**没人会发现**，
因为一个"她在忙"的假设看起来完全合理。
app-tracker 停了 28 天没人发现，就是同一类错误的另一面
（PROJECT.md 第十二节）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: 上限。**必须低于 `intent.GENERATE_THRESHOLD`（0.55）** —— 糖糖 2026-08-30 定的。
#: 躁动的出口是等，不是说
MAX = 0.5

#: 憋了多久算"憋久了"。到这个时长时间加成拉满
FULL_BONUS_AFTER = timedelta(hours=3)

#: 时间加成的上限。憋再久也只放大这么多 —— 仪器不煽情
MAX_TIME_BONUS = 1.5

#: 她在这个应用里待够这么久，才算"进入状态"。
#: 刚切过去 10 秒不算专注
FOCUS_AFTER_S = 90

#: 🔴 哪些应用算「别打扰她」。糖糖 2026-08-30 定的档位。
#:
#: ⚠️ **这张表是她定的，不是我猜的。** 改之前先问她 ——
#: 「打断她的代价有多大」只有她知道。
BUSY_TIERS: tuple[tuple[float, tuple[str, ...]], ...] = (
    #: 游戏全屏 —— 打断代价最高
    (1.0, ("delta", "三角洲", "steam", "game", "eldenring", "detroit",
           "valorant", "csgo", "genshin", "原神")),
    #: 剪辑 / 写稿 —— 在心流里
    (0.8, ("premiere", "photoshop", "davinci", "capcut", "剪映", "obs",
           "word", "excel", "code", "obsidian", "notion", "figma")),
    #: 看片 —— 能暂停，但扫兴
    (0.6, ("bilibili", "哔哩", "youtube", "iqiyi", "爱奇艺", "tencentvideo",
           "potplayer", "vlc", "netflix")),
    #: 微信 / 小红书 / 抖音 —— 本来就在碎片切换，打断成本低
    (0.25, ("wechat", "微信", "xiaohongshu", "小红书", "douyin", "抖音",
            "qq", "weibo", "微博", "telegram")),
)

#: 不在表里的应用给多少。**给低的** ——
#: 宁可漏判她在忙，也不要凭一个不认识的窗口名让他躁动
UNKNOWN_APP_BUSY = 0.3


def busy_of(app: str | None, seconds: int = 0) -> float:
    """她现在有多不方便被打断。**读不到就是 0。**

    @param app - 前台应用名（或窗口标题）。`None` = 传感器没数据
    @param seconds - 在这个窗口里待了多久
    """
    if not app:
        #: 🔴 不知道就当不忙。见模块头那段
        return 0.0

    hay = app.lower()
    busy = UNKNOWN_APP_BUSY
    for level, keys in BUSY_TIERS:
        if any(k.lower() in hay for k in keys):
            busy = level
            break

    #: 刚切过去不算专注 —— 她可能只是路过。线性爬到满
    if seconds < FOCUS_AFTER_S:
        busy *= max(0.0, seconds / FOCUS_AFTER_S)
    return busy


@dataclass
class RestlessnessState:
    """躁动。**没有自己的存量** —— 它每次都由当下的两个信号算出来。

    ⚠️ 所以这个类不落库（`to_dict` 只存"从什么时候开始憋的"）。
    憋着的话在 IntentBook 里、她在忙什么在感知层里，
    **在这儿再存一份就是第二个真源**。
    """

    #: 从什么时候开始有话没说出口。时间加成靠它
    waiting_since: datetime | None = None
    _last: dict = field(default_factory=dict)

    def value(self, want: float, app: str | None, seconds: int = 0,
              now: datetime | None = None) -> float:
        """算此刻的躁动。

        @param want - 他有多想说（pending 的 priority 之和，已经夹在 [0,1]）
        @param app - 她此刻在用什么。`None` = 不知道
        @param seconds - 在那个窗口里待了多久
        """
        now = now or datetime.now(timezone.utc)
        busy = busy_of(app, seconds)

        if want <= 0 or busy <= 0:
            #: 任何一边是 0，躁动就是 0 —— 而且要**忘掉**开始憋的时刻，
            #: 不然她忙完歇一会儿再忙，时间加成会从上次接着算
            self.waiting_since = None
            self._last = {}
            return 0.0

        if self.waiting_since is None:
            self.waiting_since = now

        waited = (now - self.waiting_since).total_seconds()
        full = FULL_BONUS_AFTER.total_seconds()
        bonus = 1.0 + (MAX_TIME_BONUS - 1.0) * min(1.0, waited / full)

        v = min(MAX, want * busy * bonus)
        self._last = {"want": round(want, 3), "busy": round(busy, 3),
                      "bonus": round(bonus, 2), "app": app,
                      "waited_min": round(waited / 60)}
        return v

    def because(self) -> list[str]:
        """凭什么觉得他躁动。**说得出来才算数。**"""
        if not self._last:
            return []
        d = self._last
        out = [f"他有话想说，而她在用 {d['app']}"]
        if d["waited_min"] >= 10:
            out.append(f"已经憋了 {d['waited_min']} 分钟")
        return out

    # ---------------------------------------------------------- 持久化
    #
    # 只存"从什么时候开始憋的"。别的都是当下算的 ——
    # 存下来就成了第二个真源，而两个真源迟早会对不上。

    def to_dict(self) -> dict:
        return {"waiting_since": self.waiting_since.isoformat()
                if self.waiting_since else None}

    @classmethod
    def from_dict(cls, data: dict | None) -> "RestlessnessState":
        st = cls()
        raw = (data or {}).get("waiting_since")
        if raw:
            try:
                st.waiting_since = datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                pass
        return st
