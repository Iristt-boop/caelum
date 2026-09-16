"""Moments 的正文生成（T4）：Drive 是**心理背景**，不是内容。

规格见 `Caelum-Moments-设计.md` 第四节，实现在 `moments/writer.py`，
范式抄 `attention/appraisal_llm.py` 的 `_ask`（utility 模型 + 失败留痕）。

## 这一组守的是什么

🔴 **糖糖点名的坑：Drive 是心理背景，不是内容。** 提示词给的是
「你此刻挺想她（因为今天聊得少）」，**不是**「写一条想念的帖子」——
否则一个月后打开全是「今天好想老婆」。第 4 条钉的就是这件事：
drive 译成气氛词进去，**数值和名字都不许进去**。

🔴 **R10：产出路径里不许出现推送。** 发帖不是开口，不弹她的锁屏。
发帖和开口长得太像了，所以第 8 条不靠自觉 —— 它读源码文本。

🔴 **超长不截断。** 截断会在句子中间断掉，而且是一次静默的内容篡改
（`docs/LOGGING.md` 禁止静默失败）。第 9 条钉的是「超了就整轮不发」。

## 每条测试能挡什么、不能挡什么

都写在各自 docstring 里（CAELUM-MAP 第三·五节：说不清自己在防什么的测试，
下次重构会被当噪音删掉）。

⚠️ 不打真网络、不调真模型：假 adapter 返回什么就等于模型返回了什么，
假 bridge 记下被调用了什么。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Turn  # noqa: E402
from tools.http import RestResult  # noqa: E402
#: 走模块名而不是直接 import 那几个函数：在 `writer.py` 里还没写出来的
#: 那一步，每条测试各自红（AttributeError），不会被一个 collection error
#: 整个盖掉 —— 红得看得见，才知道自己红的是什么
from moments import writer  # noqa: E402
from moments.writer import MAX_CHARS, MAX_RECENT, _RULES  # noqa: E402


# ---------------------------------------------------------------- 假货


class FakeAdapter:
    """把一段固定文本当成 utility 模型的输出。"""

    def __init__(self, text: str, stop_reason: str = "end"):
        self.text = text
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    def complete(self, messages, tools, **kw):
        self.calls.append({"messages": messages, "tools": tools, **kw})
        return Turn(stop_reason=self.stop_reason, text=self.text)


class DeadAdapter:
    """调用就炸 —— 模型挂了 / 网络断了。"""

    def complete(self, messages, tools, **kw):
        raise RuntimeError("模型挂了")


class FakeBridge:
    """记下被调了什么，不打网络。"""

    def __init__(self, post_result: RestResult | None = None,
                 get_result: RestResult | None = None):
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        #: ⚠️ 不能写 `post_result or ...`：`RestResult` 自己定义了 `__bool__`
        #: 返回 `ok`，所以那条**失败**的结果是 falsy，会被兜底值悄悄换掉 ——
        #: 那样这一条测的就变成了成功路径（假货测据不实，正是本文件要防的）
        self._post_result = (
            RestResult(True, {"id": "post-1"}) if post_result is None
            else post_result
        )
        self._get_result = (
            RestResult(True, {"items": []}) if get_result is None else get_result
        )

    def post(self, path, body=None):
        self.posts.append({"path": path, "body": body})
        return self._post_result

    def get(self, path, params=None):
        self.gets.append({"path": path, "params": params})
        return self._get_result


class DeadBridge:
    """连不上 bridge。"""

    def post(self, path, body=None):
        raise RuntimeError("bridge 连不上")

    def get(self, path, params=None):
        raise RuntimeError("bridge 连不上")


def _writer_source() -> str:
    """`writer.py` 的**文本** —— 第 7、8 条读的就是它（不是调用图）。"""
    return (Path(__file__).resolve().parents[1] / "moments" / "writer.py").read_text(
        encoding="utf-8")


def _warnings(caplog) -> int:
    return sum(1 for r in caplog.records if r.levelno == logging.WARNING)


# ---------------------------------------------------------------- 提示词


def test_all_four_rules_are_in_the_system_prompt():
    """四条约束**一条都不能少**，而且 `_RULES` 必须真的是四条。

    少了「不是对着她讲」，这个功能就退化成「他对着她写小作文」；
    少了「只是背景」，一个月后打开全是「今天好想老婆」；
    少了「别重复」和「两三句」，它会变成日记。

    能挡：把约束写进 `_PROMPT` 里一份、`_RULES` 里留一份空的（那样两条
          对不上），或者删掉其中任意一条；也挡「空集/半集」当通过。
    不能挡：约束的**语义**对不对 —— 机器只能看见那几个关键措辞在不在，
          「写得好不好」靠人看（这是本文件所有提示词测试的共同上限）。
    """
    system, _user = writer.build_prompt({"longing": 0.4}, [], "想留一条痕迹",
                                        "9月15日 周一 晚上21:30")

    assert len(_RULES) == 4, "空集 / 半集不是通过"
    for rule in _RULES:
        assert rule in system, f"这条约束没进 system：{rule!r}"
    #: 单钉一次这个措辞：它是整件事的边界（设计文档第四节第 1 条）
    assert "不是对着她讲" in system


def test_the_five_most_recent_posts_are_fed_in():
    """最近发过的要喂进去，**而且只喂 5 条**（照抄 speaker.py 的 `_REPEAT`）。

    它治的是同一个问题：同一件事说第三遍，重点已经不是那件事了。
    喂少了防不住复读，喂多了会把整个 prompt 撑起来。

    能挡：`recent[:1]` 这种「喂一条意思一下」、整块漏掉、或者把全部历史
          都塞进去（第 6 条会越界）。
    不能挡：喂进去之后模型真的有没有换角度 —— 那只能看线上。
    """
    recent = [f"第{i}条：今天风挺好" for i in range(1, 7)]   # 6 条，最新在前

    _system, user = writer.build_prompt({"longing": 0.4}, recent, "想留一条痕迹",
                                        "9月15日 周一 晚上21:30")

    assert MAX_RECENT == 5
    for body in recent[:MAX_RECENT]:
        assert body in user, f"最近发过的没喂进去：{body!r}"
    assert recent[MAX_RECENT] not in user, "只喂前 5 条，第 6 条不该出现"


def test_the_recent_block_is_there_even_when_nothing_was_posted():
    """`recent` 为空**也要有那一块**，写「（还没发过）」。

    整块省掉的话提示词形状每次都不一样，模型行为会跟着飘 ——
    而「形状稳定」正是这一层能拿 shadow 观察的前提。

    同一条也钉住第三块（现在几点）：两块都是「永远在场」的那种，
    一起漏就一起没。

    能挡：`if recent:` 那种「空就跳过」的写法；也挡把时钟块省掉。
    不能挡：措辞具体用哪个词（这里只认「还没发过」这个说法在不在）。
    """
    _system, user = writer.build_prompt({"longing": 0.4}, [], "想留一条痕迹",
                                        "9月15日 周一 晚上21:30")

    assert "还没发过" in user
    #: 第三块「现在几点」：给了就必须出现（带日期，和 speaker 的历史口径一致）
    assert "9月15日 周一 晚上21:30" in user


def test_a_drive_is_background_not_content():
    """🔴 drive 只当**气氛词**进去 —— 数值和名字都不许出现。

    这是「Drive 是心理背景，不是内容」在提示词上的落点：进去的是
    「你此刻挺想她」，不是「longing 0.4」。「把 longing 0.4 直接糊给模型」
    的后果就是它把这件事写进正文，一个月后全是同一句。

    同一条也钉住词表**认不出来**的那一半：认不出来就原样打印名字，
    不许消失、也不许把 0.2 那个数值带上（以后加新 drive 不用回来改词表，
    但漏了的那条得看得见它漏了）。

    能挡：把 intensities 拼进背景块、把原始 drive 名当词表缺失时的兜底
          打印、认不出来的 drive 被悄悄丢掉、以及忘了走 `DRIVE_WORDS` 词表。
    不能挡：**正文**里会不会冒出数值 —— 那要等落库之后才看得见（T5 的验收）。
    """
    _system, user = writer.build_prompt(
        {"longing": 0.4, "wanderlust": 0.2}, [], "想留一条痕迹",
        "9月15日 周一 晚上21:30")

    assert "想她" in user
    assert "wanderlust" in user, "词表认不出来的要原样打印，不能悄悄丢掉"
    assert "0.4" not in user
    assert "0.2" not in user
    assert "longing" not in user


def test_impulse_why_is_only_a_footnote():
    """`impulse_why` 只进**一行注脚**，明确写着别写进正文。

    它是给日志和帖子对得上的那根线（审计要顺着它点回那条帖），
    不是素材 —— 原样交给模型当素材，它就只会把「内心 0.64 × 时机 0.97」
    复述进正文。

    能挡：把 `impulse_why` 塞进心理背景块（那样它会当素材用）、
          或者干脆忘了带进去（那样日志和帖子对不上）。
    不能挡：模型会不会真的照做 —— 那是提示词的事，不是这里能验的。
    """
    why = "想她 0.40 领头（内心 0.64 × 时机 0.97 = 0.62），过阈值 0.45"

    _system, user = writer.build_prompt({"longing": 0.4}, [], why,
                                        "9月15日 周一 晚上21:30")

    assert why in user
    line = next(l for l in user.splitlines() if why in l)
    assert "别写进正文" in line, f"注脚那一行没写明它不是素材：{line!r}"


# ---------------------------------------------------------------- 生成


def test_without_a_utility_model_nothing_is_generated(caplog):
    """没有 utility 模型就**什么都不做**，而且留一条 INFO。

    🔴 绝不退回主模型：这一层该用哪个模型由 `NOX_UTILITY_MODEL` 一处说了算，
    悄悄退回主模型的话，哪天主模型换成贵的，这条线会跟着涨价而没人知道
    （`appraisal_llm.py:287-296` 的原话）。它的价值是「多一层表达」，
    不是「必须有」—— 缺了就不做。

    能挡：`adapter_ref` 取不到时退回别的模型、以及**静默**返回 None
          （那样线上「怎么好久没发帖了」查不出来）。
    不能挡：**选哪个模型**对不对（那是 `NOX_UTILITY_MODEL` 的事，不是这里）。
    """
    with caplog.at_level(logging.INFO):
        assert writer.generate(None, {"longing": 0.4}, [], "想留一条痕迹") is None
        #: 传取值函数、函数返回 None —— 和直接传 None 是同一件事
        assert writer.generate(
            lambda: None, {"longing": 0.4}, [], "想留一条痕迹") is None

    assert any(r.levelno == logging.INFO for r in caplog.records), "静默了"


def test_the_writer_never_falls_back_to_the_primary_model():
    """🔴 源码里不许出现任何指向主模型的东西 —— 分工要稳定。

    和 `test_moments_impulse.py` 最后一条同一个做法、同一个理由：
    靠自觉的话，下一次「顺手加个兜底」就把它变成主模型的第五条出口。

    能挡：写一行 `adapter = None  # primary` 这种兜底、或者去 import
          router / core 拿主 adapter。
    不能挡：**间接**依赖（它读的是这个文件的文本，不是调用图）——
          间接那条靠 import 白名单，不在 v1。
    """
    source = _writer_source()

    for banned in ("primary", "main_adapter", "router.chat", "core.chat"):
        assert banned not in source, f"writer.py 里不该出现 {banned!r}"


def test_r10_the_producing_path_has_no_push():
    """🔴 R10：产出路径里没有推送。发帖不是开口，不弹她的锁屏。

    发帖和开口长得太像了（都是「他主动说了句话」），所以这条不靠自觉。
    产出路径里出现推送，等于把它变成第五条主动消息渠道
    （糖糖 2026-08-18：「不要成为第五条主动消息渠道」）。

    能挡：往这个文件里塞一行推送调用。
    不能挡：**别的**渠道转发它（比如 loop 拿到正文之后去推送）——
          那要靠 T7 那条扫整个 `moments/` 的哨兵。
    """
    source = _writer_source()

    for banned in ("push", "send", "notify"):
        assert banned not in source, f"R10：writer.py 里不该出现 {banned!r}"


def test_an_overlong_body_is_not_posted_but_the_exact_limit_is(caplog):
    """超长**不截断**：整轮不发 + WARNING。刚好 140 字要能过。

    截断会在句子中间断掉，而且是一次**静默的内容篡改**（`docs/LOGGING.md`
    禁止静默失败），所以这里选的是「不发」。`reason` 走 record.py 的
    `write_failed` —— 它和「骰子没中」要能分开数。

    边界两侧都测是故意的：只测「200 字不发」的话，把判据改成 `>=`
    （140 字也发不出去）照样是绿的。

    能挡：静默截断、`>` 写成 `>=`、以及超长时只打日志却把正文返回出去。
    不能挡：140 这个数合不合适（那是糖糖定的）。
    """
    long_adapter = FakeAdapter("好" * 200)
    with caplog.at_level(logging.WARNING):
        assert writer.generate(
            long_adapter, {"longing": 0.4}, [], "想留一条痕迹") is None
    assert _warnings(caplog) >= 1, "超长不发了，却一个字都没留"

    exact_adapter = FakeAdapter("好" * MAX_CHARS)
    assert writer.generate(
        exact_adapter, {"longing": 0.4}, [], "想留一条痕迹") == "好" * MAX_CHARS


def test_an_empty_body_leaves_a_trace(caplog):
    """HTTP 200 但 `text` 是空的 —— **已知形状，必须留痕**。

    会思考的模型 reasoning 和正文抢 max_tokens，`stop_reason` 看着正常、
    正文却是空的（这个项目栽过）。不能把它当成「他没什么想说的」：
    那样线上表现是「好久没发帖了」，而日志里一片安静，查不出来。

    能挡：把空文本当成正常结果（返回空串）、或者不记 warning。
    不能挡：别的空返回形状（`stop_reason="refusal"` 走的是同一条分支，
          这里只钉住最常见的那一种）。
    """
    empty_adapter = FakeAdapter("", stop_reason="end")

    with caplog.at_level(logging.WARNING):
        assert writer.generate(
            empty_adapter, {"longing": 0.4}, [], "想留一条痕迹") is None

    assert _warnings(caplog) >= 1, "空正文却没留痕"


def test_an_adapter_failure_is_logged_and_swallowed(caplog):
    """调用炸了：留 WARNING，**异常不许往外冒**，返回 None。

    往外冒的话会把整个 post_tick 掀掉（心跳台账那条线也跟着断）。
    但不许静默 —— 「他好像最近不怎么发帖了」这种表现没有任何报错，
    不留痕就永远查不出来（`docs/LOGGING.md`）。

    能挡：吞掉异常却什么都不记、或者干脆不 catch。
    不能挡：`turn.stop_reason == "error"` 那条分支（那是第 10 条旁边的事，
          这里测的是抛出来的异常）。
    """
    with caplog.at_level(logging.WARNING):
        assert writer.generate(
            DeadAdapter(), {"longing": 0.4}, [], "想留一条痕迹") is None

    assert _warnings(caplog) >= 1, "炸了却没留痕"


def test_paired_quotes_are_stripped():
    """成对引号要剥掉 —— 模型爱把整句套一层引号。

    不剥的话，正文落库就带着引号，她看见的是「他说的话被加了引号」，
    读起来像引用别人。

    能挡：只 `strip()` 空白（那样 `「今天风挺好」` 原样返回）。
    不能挡：不成对的引号（比如只有左半边）—— 那种故意不剥，
          乱剥会把正文里的引号吃掉。
    """
    for quoted, expected in (("「今天风挺好」", "今天风挺好"),
                             ('"今天风挺好"', "今天风挺好"),
                             ("“今天风挺好”", "今天风挺好")):
        #: 顺手走一遍「传取值函数」那条路：`LLMAppraiser` 就是这么接的，
        #: 两条形状都得通
        out = writer.generate(
            lambda: FakeAdapter(quoted), {"longing": 0.4}, [], "想留一条痕迹")
        assert out is not None, f"{quoted!r} 被整段丢掉了"
        assert out == expected, f"{quoted!r} 剥引号剥成了 {out!r}"
        for mark in "「」“”":
            assert mark not in out


# ---------------------------------------------------------------- 落库


def test_the_body_lands_with_exactly_these_fields():
    """落库的 body 形状**逐字钉死**：六个键，一个不多一个不少。

    多了键 → bridge 那边会多一列 / 静默丢弃；少了键 → 帖子和审计对不上。
    三个值各有各的理由：`kind="moment"` 让她的日记和他的碎片在同一个
    时间流里区分得开；`author="Nox"` 是他自己发的（`triggerAiComment`
    只给糖糖配评论，他的帖不该自问自答）。

    🔴 `mood` 传**空串**：`mood` 是她写日记时自己选的心情，不许拿它存
    drive（设计文档第二节点名过）。`drive` 传的是**主导那个 drive 的名字**
    （`"longing"`），不是气氛词、不是数值 —— 渲染成气氛词是前端的事。

    能挡：键集合漂了、`mood` 被拿存 drive、`kind` 忘了传、
          `drive` 传成中文气氛词。
    不能挡：bridge 那边真实落库的结果（那是 T1 的测试）。
    """
    bridge = FakeBridge()

    pid = writer.post(bridge, "今天风挺好", "longing", "想留一条痕迹")

    assert pid == "post-1"
    assert len(bridge.posts) == 1
    assert bridge.posts[0]["path"] == "/api/diary"
    body = bridge.posts[0]["body"]
    assert set(body) == {"content", "author", "kind", "drive", "impulse_why", "mood"}
    assert body["content"] == "今天风挺好"
    assert body["author"] == "Nox"
    assert body["kind"] == "moment"
    assert body["drive"] == "longing"
    assert body["impulse_why"] == "想留一条痕迹"
    assert body["mood"] == ""


def test_a_write_that_fails_does_not_return_an_id(caplog):
    """落库没成 / 没拿到 id：返回 None + WARNING —— 不许假装发了。

    `post_id` 是这条记录和真实那条帖之间**唯一的缝**（`record.py` 的原话）：
    空着就是审计断链，几天后点不回那条帖。所以宁可返回 None 让 loop 记
    `write_failed`，也不能编一个 id 出来。

    两种失败都得挡：`ok=False`（网络 / 400），以及 `ok=True` 但 body 里
    没有 `id`（bridge 改了返回形状，静默坏）。

    能挡：只判 `r.ok` 不判 id、或者失败时返回空串。
    不能挡：返回了 id 但那条帖其实没进库（那要 db 层才验得了）。
    """
    refused = FakeBridge(post_result=RestResult(False, error="boom"))
    with caplog.at_level(logging.WARNING):
        assert writer.post(refused, "今天风挺好", "longing", "想留一条痕迹") is None

    idless = FakeBridge(post_result=RestResult(True, {}))
    with caplog.at_level(logging.WARNING):
        assert writer.post(idless, "今天风挺好", "longing", "想留一条痕迹") is None

    assert _warnings(caplog) >= 2, "两次失败都要留痕"


# ---------------------------------------------------------------- 防复读


def test_recent_posts_hits_the_right_endpoint(caplog):
    """防复读的料要打**对的接口**、带对的参数，失败一律返回 `[]`。

    `author="Nox"` 是必须的：不筛作者的话，她自己的日记也会被当成
    「他最近发过的」，于是他开始躲她早就说过的话。
    `limit=5` 和喂给模型的条数对齐（`MAX_RECENT`）。

    少了防复读比不发帖好（所以失败返回 `[]` 而不是抛），但**必须留痕** ——
    否则「他最近怎么老重复」查出来是这个接口 403 了。

    能挡：打错接口、参数名漂了、item 里没 `body` 也照收、
          失败时抛异常、失败时静默返回空。
    不能挡：bridge 那边排序对不对（「最新在前」是它的约定，T1 验）。
    """
    bridge = FakeBridge(get_result=RestResult(True, {"items": [
        {"body": "今天风挺好"}, {"body": ""}, {"body": "   "}, {"body": "楼下那只猫"},
    ]}))

    assert writer.recent_posts(bridge) == ["今天风挺好", "楼下那只猫"]
    assert bridge.gets[0]["path"] == "/api/moments"
    assert bridge.gets[0]["params"]["author"] == "Nox"
    assert bridge.gets[0]["params"]["limit"] == 5

    with caplog.at_level(logging.WARNING):
        #: 三种失败：接口说不 ok、结构不对、连不上 —— 都得留痕 + 返回 []
        #: ⚠️ 一律用关键字：`FakeBridge` 的第一个参数是 `post_result`，
        #: 位置传参会把失败结果塞到 `post()` 那边去，`get()` 于是走了成功路径 ——
        #: 断言照样绿，但测的已经不是这件事了
        assert writer.recent_posts(
            FakeBridge(get_result=RestResult(False, error="boom"))) == []
        assert writer.recent_posts(
            FakeBridge(get_result=RestResult(True, None))) == []
        assert writer.recent_posts(
            FakeBridge(get_result=RestResult(True, {"no_items": 1}))) == []
        assert writer.recent_posts(DeadBridge()) == []

    assert _warnings(caplog) >= 4, "四次失败都要留痕"


# ---------------------------------------------------------------- 词表搬家


def test_drive_words_moved_to_the_package_root():
    """`DRIVE_WORDS` 搬到了 `moments/__init__.py`，**而且旧的那份没了**。

    两边各留一份词表必然会各自漂移 —— 帖子里说「想她」、`why` 里说
    「想老婆」，而测试只盯得住一份。

    能挡：搬了一份、旧的 `_WORDS = {` 还留在 `impulse.py` 里
          （那种情况这里照样能 import，只有源码断言挡得住）。
    不能挡：两边都从 `DRIVE_WORDS` 拿、但其中一边又悄悄 `.get` 兜底
          别的词的写法。
    """
    from moments import DRIVE_WORDS

    assert DRIVE_WORDS["longing"] == "想她"

    impulse_source = (
        Path(__file__).resolve().parents[1] / "moments" / "impulse.py"
    ).read_text(encoding="utf-8")
    assert "_WORDS = {" not in impulse_source, "旧的那份还留着，迟早各自漂移"
