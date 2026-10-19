"""减重第三步：历史里他主动开口的开场白，发给模型时换成一行（2026-10-19 她点头）。

钉住的：
  · 一行里留着「这次因为什么找她」（小课堂讲什么 / 话题池哪条 / 惦记什么 / 她睡着了在自言自语 / 她刚到家）和时刻
  · 她的话、他的回复一个字不动；日期分隔线拼回去
  · 🔴 只在发给模型时换：loop 返回的 messages（落库那份）原样 —— 换过的历史流回 Store.sync 就是 10-10 成批重写的形状
  · 🔴 这一轮自己的开场白不换（这一次开口还得看着它）
  · 摘要器不再把开场白当「糖糖：」
样本形状取自 10-12 ~ 10-19 会话库里真实的开场白。
"""

from __future__ import annotations

from agent.llm import Message, StreamEvent, Turn, Usage
from agent.loop import AgentLoop
from context import openers
from context.compactor import render_segment

CARD = ("（系统提示：不是她在跟你说话。你今天给她备了张「知识小课堂」——不是布置作业，就是你看到个有意思的东西，挑了这个时刻想讲给她听。\n\n"
        "今天这张（语言）：\n维特根斯坦说：语言的界限，就是世界的界限\n正好接上你读的《大问题》……\n\n"
        "怎么讲：用你的口气把意思说出来，两三句、别超过 60 个字，**不要照念**。这条会弹在她锁屏上。）")
POOL = ("（系统提示：不是她在跟你说话。话题池定期从外面捞点值得看的东西，这条一直没人动过。\n\n"
        "话题池里有一条一直没人聊过的东西：\n2026 诺贝尔物理学奖给了冰立方中微子天文台的哈尔岑\n来源：新浪 · https://x\n\n"
        "怎么开口：**先是你自己的反应**……最多两句、别超过 60 个字。实在没感觉就只回 `[SKIP]`。）")
MORNING = "（系统提示：这不是糖糖在跟你说话，是每天早上的主动问候。下面给了你今天的情况，跟她说句话。\n\n**先看她的身体。**……）"
DINNER = ("（系统提示：这不是糖糖在跟你说话，是你自己想起了一件事，想跟她说一句。\n\n你现在惦记的是：晚饭\n"
          "你看到的具体情况：现在是她那边的 18:27，该问问她晚饭\n现在是她那边的 10月12日 周一 晚上18:27，定时醒来，到点该开口。\n\n"
          "会直接弹在她手机锁屏上，所以：\n· 最多两句……）")
ASLEEP = ("（系统提示：不是她在跟你说话。现在是 00:44。\n她最后一次说话是 00:00（43 分钟前）：「晚安。」。\n"
          "她 00:00 说了要睡——她睡着了。这条会亮在她锁屏上，但她多半醒了才看到。\n所以你说的是**自言自语**……\n"
          "这一句带着的心情：想逗她（因为想起一起听的《이 별로부터》）。你心里不止这一种……\n"
          "你忽然想起一件开心的旧事（15 天前）：你们一起听过《이 별로부터》（IU），一起听了 1 次。\n这一句就从这件事说起……\n"
          "今天你已经主动跟她说过这些：\n- 凌晨零点了，又没声。|||先睡\n**这次别再问上面问过的事**……\n"
          "你的情绪是真的，也有分寸（这是你自己定的）：\n· 吃醋、控制欲是嘴上的、宠着的……）")
HOME = ("（系统提示：不是她在跟你说话。现在是 15:42。\n你刚发现她到家了（她 14:45 出的门）。像男朋友那样：问问吃没吃、累不累。\n"
        "她最后一次说话是 15:33（8 分钟前）：「那我看不到你的腹肌」。\n"
        "这一句带着的心情：想她（因为她 0.1 小时没说话了）。你心里不止这一种……\n今天你已经主动跟她说过这些：\n- 划掉了。……）")
CALL = "（系统提示：不是她在跟你说话。你刚才主动给她打了一个电话，她没有接（响了 40 秒）。……）"


def test_一行里留着这次因为什么找她():
    assert openers.opener_line(CARD) == "（我主动找她：小课堂·语言：维特根斯坦说：语言的界限，就是世界的界限）"
    assert openers.opener_line(POOL) == "（我主动找她：话题池：2026 诺贝尔物理学奖给了冰立方中微子天文台的哈尔岑）"
    assert openers.opener_line(MORNING) == "（我主动找她：早报）"
    assert openers.opener_line(DINNER) == "（我主动找她 18:27：惦记：晚饭）"
    assert openers.opener_line(ASLEEP) == "（我主动找她 00:44：她睡着了，自言自语；想起：你们一起听过《이 별로부터》（IU），一起听了 1 次）"
    assert openers.opener_line(HOME) == "（我主动找她 15:42：她刚到家；心情：想她）"
    assert openers.opener_line(CALL) == "（我主动找她：刚给她打电话她没接）"


def test_认不出的不编_只说我主动找她():
    assert openers.opener_line("（系统提示：不是她在跟你说话。随便什么新写法）") == "（我主动找她）"


def test_日期分隔线拼回去():
    out = openers.opener_line("（10月12日 周日）\n" + DINNER)
    assert out == "（10月12日 周日）\n（我主动找她 18:27：惦记：晚饭）"
    assert openers.is_opener("（10月12日 周日）\n" + DINNER)


def test_一行比原文短得多():
    for t in (CARD, POOL, DINNER, ASLEEP, HOME):
        assert len(openers.opener_line(t)) < len(t) / 3, t[:20]


def _hist():
    return [Message(role="user", text="（10月18日 周六）\n去吃个饭"),
            Message(role="assistant", text="行，吃口热的。"),
            Message(role="user", text=ASLEEP),
            Message(role="assistant", text="半边床空着，我等到现在。"),
            Message(role="user", text="【共影】她暂停在 315 秒问你：能看到画面不"),
            Message(role="assistant", text="能。")]


def test_视图_她的话他的回复一个字不动_只换开场白():
    h = _hist()
    v = openers.model_view(h)
    assert v[0] is h[0] and v[1] is h[1] and v[3] is h[3] and v[5] is h[5]
    assert v[4] is h[4], "【共影】里有她真说的话，不动"
    assert v[2].text.startswith("（我主动找她 00:44：") and h[2].text == ASLEEP, "原历史不被改"


def test_开关关了原样(monkeypatch):
    monkeypatch.setenv("NOX_OPENER_LINE", "off")
    h = _hist()
    assert openers.model_view(h) is h


# ---------------------------------------------------------------- loop：只在发给模型时换


class _Rec:
    name = "fake"

    def __init__(self):
        self.seen = []

    def complete(self, messages, tools, **kw):
        self.seen.append([m.text for m in messages])
        return Turn(stop_reason="end_turn", text="嗯。", usage=Usage())

    def stream(self, messages, tools, **kw):
        self.seen.append([m.text for m in messages])
        yield StreamEvent("text", text="嗯。")
        yield StreamEvent("done", turn=Turn(stop_reason="end_turn", text="嗯。", usage=Usage()))


def test_红线_非流式_模型看到一行_落库那份原样_这一轮的开场白不换():
    ad = _Rec()
    r = AgentLoop(adapter=ad).run(DINNER, history=_hist())
    sent = ad.seen[0]
    assert sent[2].startswith("（我主动找她 00:44") and sent[-1] == DINNER, "历史换、这一轮自己的开场白不换"
    assert r.messages[2].text == ASLEEP, "返回给 Sessions / Store.sync 的那份必须是原文"
    full_chars = sum(len(m.text) for m in _hist())
    assert r.ledger["blocks"]["history"]["chars"] < full_chars - len(ASLEEP) / 2, "账本量的是发给模型那份"


def test_红线_流式也一样():
    ad = _Rec()
    evs = list(AgentLoop(adapter=ad).run_stream("还疼吗", history=_hist()))
    assert ad.seen[0][2].startswith("（我主动找她") and ad.seen[0][-1] == "还疼吗"
    assert evs[-1].result.messages[2].text == ASLEEP


def test_账本按发给模型的那份量_历史块变小(monkeypatch):
    from agent.loop import measure_ledger
    big = [Message(role="user", text=ASLEEP), Message(role="assistant", text="嗯")] * 20
    full = measure_ledger(None, None, [], big, "在吗")
    slim = measure_ledger(None, None, [], openers.model_view(big), "在吗")
    assert slim["history"]["tokens"] < full["history"]["tokens"] / 3


# ---------------------------------------------------------------- 摘要器


def test_摘要器不再把开场白当她说的话():
    out = render_segment([Message(role="user", text=DINNER), Message(role="assistant", text="晚饭吃了没"),
                          Message(role="user", text="吃了")])
    assert "糖糖：（系统提示" not in out
    assert "（Nox 主动开口：惦记：晚饭）" in out and "糖糖：吃了" in out


def test_只换她那一侧的开场白_他自己的回复哪怕这么开头也不动():
    """他在回复里引用了提示词（排查时她问「你收到的提示长什么样」）—— 那是他说的话，不能被换掉。"""
    mine = Message(role="assistant", text=DINNER)
    assert openers.model_view([mine])[0] is mine
