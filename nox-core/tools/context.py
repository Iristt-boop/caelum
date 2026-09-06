"""工具执行上下文 —— 收集「除了返回文本之外还要做的事」。

为什么需要它：大部分工具返回一段文字给模型就够了，但有些工具的意义
在于**产生一个副作用**。比如 send_gallery_image，它真正要做的是让一张
图出现在聊天里 —— 而 Core 是独立进程，够不着 bridge 那条 SSE 连接。

所以工具把「要附带发出去的东西」记在这里，随 LoopResult 一起交给调用方，
由 API 层输出、bridge 转成前端认识的事件。

用 contextvars 而不是全局变量或参数：
  - 全局变量在并发请求之间会串
  - 加参数要改 ToolHandler 的签名和所有现有工具

⚠️ 但**绑定范围必须只包住一次工具调用，中间不能有 yield**。

踩过的坑：一开始把 scope() 包在 run_stream 外面，本地测试全过，
上线一调工具就 `ValueError: Token was created in a different Context`。
原因是 Starlette 用线程池逐步推同步生成器，**每次 next() 都在自己
复制的 Context 里跑** —— 在第一次 next() 里 set 的 token，到最后一次
next() 里 reset 就不是同一个 Context 了。顺带一提，那个 set 对中间几步
其实也没生效，工具本来就看不见上下文。

现在改成：ToolContext 由 loop 当**局部变量**持有（生成器帧天然按调用
隔离，并发不串），只在 _execute 里 bind 一下。set 和 reset 之间是一段
连续的同步代码，无论 Context 怎么复制都成立。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ToolContext:
    """一次对话轮次里，工具产生的附带产物。"""

    attachments: list[dict[str, Any]] = field(default_factory=list)

    def attach_image(self, url: str, **meta: Any) -> None:
        """标记「这张图要发到聊天里」。

        只放 URL 和元数据，不放图片内容 —— 内容由 bridge 从磁盘读，
        走 Core 这一趟会把几百 KB 的 base64 在进程间搬来搬去。
        """
        self.attachments.append({"type": "image", "url": url, **meta})

    def attach_voice(self, tts: str, zh: str = "", **meta: Any) -> None:
        """标记「这句要作为语音条发出去」。

        tts 是带 ElevenLabs 情绪标签的原文（给合成用），zh 是中文对照。
        真正合成在 bridge 那边做 —— Core 不碰音频。
        """
        self.attachments.append({"type": "voice", "tts": tts, "zh": zh, **meta})

    def attach_music(self, song_id: str, name: str = "", artist: str = "",
                     cover: str = "", **meta: Any) -> None:
        """标记「这首歌要作为音乐卡片发到聊天里」。

        只放元数据，**不放音频** —— 同 attach_image 的道理，音频几 MB，
        走 Core 这一趟等于在进程间搬文件。前端拿 song_id 去 bridge 的
        代理接口取流（eryu 的 /music/* 都要鉴权，而 <audio src> 带不了
        header，所以必须由 bridge 代理，token 不能落到前端）。
        """
        self.attachments.append({
            "type": "music", "song_id": str(song_id),
            "name": name, "artist": artist, "cover": cover, **meta,
        })

    def attach_order(self, order_id: str, card: dict[str, Any], **meta: Any) -> None:
        """标记「这张待确认单要作为卡片发到聊天里」（2026-09-06）。

        🔴 `card` 是**给她看的那一份**，不含券码、不含付款链接。
        下单要用的参数（`couponCodeList` 等）留在库里，不跟着卡走 ——
        卡会被截图、会进日志，没必要把她账号里的东西带出去。

        付款链接更是绝对不进这里：`Caelum-AI支付-可行性调研.md` 第六节
        「收银台 URL 不进日志、不进聊天历史、不进数据库明文」。
        """
        self.attachments.append({
            "type": "order", "order_id": order_id, "card": card, **meta,
        })

    def attach_meme(self, tag: str, **meta: Any) -> None:
        """标记「这个表情要发到聊天里」。

        tag 是表情名（memes.js 的键，如「开心」「晚安」）。bridge 转成前端
        的 meme 事件，前端用 tag 查 memes 表得到图片 URL。
        """
        self.attachments.append({"type": "meme", "tag": tag, **meta})


_current: ContextVar[ToolContext | None] = ContextVar("nox_tool_context", default=None)


def current() -> ToolContext | None:
    """取当前轮次的上下文。不在轮次里时返回 None ——
    工具要能容忍这种情况（比如被单元测试直接调用）。"""
    return _current.get()


@contextmanager
def bind(ctx: ToolContext) -> Iterator[ToolContext]:
    """把上下文绑到当前执行上下文，只包住一次工具调用。

    **调用方内部不能有 yield** —— 见本文件开头那个坑。
    """
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def scope() -> Iterator[ToolContext]:
    """新建一个上下文并绑上。只给不涉及生成器的地方用（测试、脚本）。

    loop 里**不要用这个** —— 它要跨 yield，得用 bind + 局部变量。
    """
    with bind(ToolContext()) as ctx:
        yield ctx
