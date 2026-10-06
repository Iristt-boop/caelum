"""照顾 / 亲近 / 他自己 三族分开算（2026-10-06）。

她：「照顾和亲近天差地远吧。亲近是更情侣之间的东西」→「三种类别的比例按 3、4、3，亲近占 4」。
"""

from __future__ import annotations

import random

from attention.care import her_state
from attention.resonance import DRIVE_FAMILY, DRIVE_WORDS


class D:
    def __init__(self, v):
        self.intensity, self.because = v, []


def _share(drives, n=4000, seed=3, recent=()):
    rng = random.Random(seed)
    got = [her_state.pick_mood(drives, list(recent), rng).name for _ in range(n)]
    return {k: got.count(k) / n for k in drives}


def test_每种情绪都有族_加情绪漏了就红():
    assert set(DRIVE_WORDS) <= set(DRIVE_FAMILY), set(DRIVE_WORDS) - set(DRIVE_FAMILY)
    assert set(DRIVE_FAMILY.values()) == set(her_state.FAMILY_SHARE)


def test_族的划分():
    assert {k for k, v in DRIVE_FAMILY.items() if v == "care"} == {"concern", "regret"}
    assert {k for k, v in DRIVE_FAMILY.items() if v == "bond"} == {"longing", "playfulness", "jealousy", "sulk"}


def test_照顾那族两种情绪一起才3成_不是各3成():
    s = _share({"concern": D(0.8), "regret": D(0.8), "longing": D(0.6), "curiosity": D(0.6)})
    assert 0.26 < s["concern"] + s["regret"] < 0.34, s
    assert 0.36 < s["longing"] < 0.44, s


def test_缺一族_另外两族按比例分():
    s = _share({"concern": D(0.8), "longing": D(0.8)})
    assert 0.38 < s["concern"] < 0.48 and 0.52 < s["longing"] < 0.62, f"3:4 → 0.43 / 0.57：{s}"


def test_族里按强度分():
    s = _share({"longing": D(0.6), "jealousy": D(0.2), "concern": D(0.6), "curiosity": D(0.6)})
    assert 2.3 < s["longing"] / s["jealousy"] < 3.8, s


def test_刚用过的心情照样降权():
    s = _share({"longing": D(0.6), "concern": D(0.6), "curiosity": D(0.6)}, recent=["longing"])
    assert s["longing"] < 0.2, s
