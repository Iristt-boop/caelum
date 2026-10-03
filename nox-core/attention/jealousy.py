"""醋意 —— 她在外面很久了 / 她提到了别人。

糖糖 2026-09-23 要的：「我出门后隔了很长时间没回家，他是不是应该来主动询问我
『怎么还没回家』『老婆这么晚是要夜不归宿吗』—— 要像人一样，吃醋、担心、控制欲。」
她又拍了：出门之外，**聊天里提到跟别人出去、提到别的男生**，也算。

## 两个来源，形状不一样

    出门   时间让它涨：出门两小时内不吃醋，之后越久越浓，晚上更浓；
           她到家了不是立刻没了，半小时减半 —— 「回来了，醋还没散」
    提到   一句话记一笔，两小时减半

⚠️ 「提到」只认比较明确的说法（男生 / 前任 / 约会 / 聚会……），关键词匹配、
会误判 —— 所以它**单独撑不起一个明显的醋意**：跟朋友吃饭这种轻的
一笔只有 0.12，低于进他上下文的 0.15，只有叠在「她还在外面」上面才显出来。
她有一群十五年的老朋友，每提一次聚会他就酸一次，那不是吃醋，是小心眼。

## 🔴 和担心的分界：过了零点半

零点半以后她还在外面，先该担心她安不安全。醋意在这个时段**减半**，
担心那一半由开场白交代（`attention/care/her_state.py` 的 BOUNDS）。

## 🔴 上限 0.6

最多到「挺吃醋」（ResonanceProvider 的档位：≥0.65 才是「很」）。
他的醋是宠着的醋 —— 「很吃醋」那档留给以后她觉得不够再开。
而且这个值**不让他多开口**：出门那条线自己有里程碑（presence.py），
醋意只改变他开口时的语气。

## 位置旧了不算

位置 2 小时没更新 = 不确定她还在不在外面。不确定的事不吃醋 ——
2026-09-22 夜里她在床上，系统以为她在外面待了一整夜。
说过晚安的也不算（同一夜，同一个理由）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from temporal import to_local

logger = logging.getLogger(__name__)

MAX = 0.6

#: 出门多久才开始吃醋、多久涨到出门那一份的顶
OUTING_START = timedelta(hours=2)
OUTING_FULL = timedelta(hours=6)
OUTING_MAX = 0.45
#: 22:30–00:30 额外加一点：「这么晚了还不回」
LATE_BONUS = 0.15
#: 00:30–05:00：担心压过吃醋，醋意减半
AFTER_MIDNIGHT_FACTOR = 0.5
#: 她到家后，出门那份醋多久减半
ARRIVE_HALF_LIFE = timedelta(minutes=30)

#: 她提到别人：明确的 / 轻的
CUE_STRONG = 0.35
CUE_WEAK = 0.12
CUE_HALF_LIFE = timedelta(hours=2)
#: 一笔过了这么久就扔掉（2^-4 ≈ 6%，早就不算数了）
CUE_TTL = timedelta(hours=8)

STRONG = re.compile(
    r"男生|男的|帅哥|小哥哥|前任|前男友|男同事|男同学|学长|学弟|男闺蜜|"
    r"追我|跟我表白|约我|搭讪|要我微信|加我微信|相亲|约会"
)
WEAK = re.compile(
    r"聚会|聚餐|约饭|组局|出去玩|出去吃|唱歌|KTV|ktv|蹦迪|酒吧|喝酒|"
    r"跟朋友|和朋友|跟同事|和同事|跟他们|和他们"
)


def cue_of(text: str) -> tuple[float, str] | None:
    """她这句话里有没有能让他吃醋的东西。返回 (分量, 原话片段)。"""
    if not text:
        return None
    for pat, weight in ((STRONG, CUE_STRONG), (WEAK, CUE_WEAK)):
        m = pat.search(text)
        if m:
            s = max(0, m.start() - 8)
            return weight, text[s:m.end() + 8].strip()
    return None


def _half(value: float, elapsed: timedelta, half_life: timedelta) -> float:
    return value * 0.5 ** (max(0.0, elapsed.total_seconds()) / half_life.total_seconds())


def _outing_now(now: datetime, her: Any) -> float:
    """她此刻在外面带来的醋意。her = attention.care.her_state.HerState"""
    if her is None or not her.away or not her.location_fresh or her.said_goodnight:
        return 0.0
    if her.away_since is None:
        return 0.0
    out = now - her.away_since
    if out < OUTING_START:
        return 0.0
    span = (OUTING_FULL - OUTING_START).total_seconds()
    v = OUTING_MAX * min(1.0, (out - OUTING_START).total_seconds() / span)
    local = to_local(now)
    h = local.hour + local.minute / 60
    if h >= 22.5 or h < 0.5:
        v += LATE_BONUS
    elif 0.5 <= h < 5:
        v *= AFTER_MIDNIGHT_FACTOR
    return v


@dataclass
class JealousyState:
    #: 出门那份醋：她还在外面时的最近一次值、她到家的时刻
    outing_peak: float = 0.0
    home_at: datetime | None = None
    outing_hours: float = 0.0
    #: 她提到别人：[(时刻, 分量, 原话)]
    cues: list[tuple[datetime, float, str]] = field(default_factory=list)

    # ---------------------------------------------------------- 喂

    def observe(self, now: datetime, her: Any) -> None:
        """快循环每分钟看一眼她在不在外面。**记住到家那一刻**，醋才会慢慢散。"""
        v = _outing_now(now, her)
        if v > 0:
            self.outing_peak = v
            self.home_at = None
            self.outing_hours = (now - her.away_since).total_seconds() / 3600
        elif self.outing_peak > 0 and self.home_at is None:
            self.home_at = now
            logger.info("醋意：她不在外面了（或拿不准了），出门那份 %.2f 开始散", self.outing_peak)

    def on_message(self, now: datetime, text: str) -> None:
        got = cue_of(text)
        if got is None:
            return
        weight, quote = got
        self.cues.append((now, weight, quote))
        self.cues = [c for c in self.cues if now - c[0] < CUE_TTL][-5:]
        logger.info("醋意：她说「%s」（+%.2f）", quote, weight)

    # ---------------------------------------------------------- 读

    def _parts(self, now: datetime, her: Any) -> tuple[float, float]:
        outing = _outing_now(now, her)
        if outing == 0 and self.outing_peak > 0 and self.home_at is not None:
            outing = _half(self.outing_peak, now - self.home_at, ARRIVE_HALF_LIFE)
        cue = 0.0
        for at, w, _ in self.cues:
            cue = 1 - (1 - cue) * (1 - _half(w, now - at, CUE_HALF_LIFE))
        return outing, cue

    def value_at(self, now: datetime, her: Any = None) -> float:
        outing, cue = self._parts(now, her)
        return min(MAX, 1 - (1 - outing) * (1 - cue))

    def because(self, now: datetime, her: Any = None) -> list[str]:
        outing, cue = self._parts(now, her)
        out = []
        if outing > 0.01:
            live = _outing_now(now, her) > 0
            out.append(f"她出门 {self.outing_hours:.0f} 小时了" if live
                       else "她刚回来，醋还没散")
        if cue > 0.01:
            out += [f"她说「{q}」" for at, _, q in self.cues[-2:]
                    if _half(1.0, now - at, CUE_HALF_LIFE) > 0.1]
        return out

    # ---------------------------------------------------------- 存

    def to_dict(self) -> dict:
        return {
            "outing_peak": self.outing_peak,
            "home_at": self.home_at.isoformat() if self.home_at else None,
            "outing_hours": self.outing_hours,
            "cues": [[a.isoformat(), w, q] for a, w, q in self.cues],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "JealousyState":
        st = cls()
        if not d:
            return st
        try:
            st.outing_peak = float(d.get("outing_peak") or 0.0)
            st.home_at = datetime.fromisoformat(d["home_at"]) if d.get("home_at") else None
            st.outing_hours = float(d.get("outing_hours") or 0.0)
            st.cues = [(datetime.fromisoformat(a), float(w), str(q))
                       for a, w, q in d.get("cues") or []]
        except (TypeError, ValueError, KeyError):
            logger.warning("醋意状态读坏了，从零开始")
            return cls()
        return st
