# -*- coding: utf-8 -*-
"""共影的眼睛 —— Gemini 视频理解 + 花销账本。

## 为什么从「一帧」变成「一段」

原来的做法是 ffmpeg 抽 T 时刻的**一张图**丢给 vision 模型。
一张图看不见「刚刚发生了什么」：谁转身走了、镜头往哪儿推、
那句台词是谁说的。共影要的恰恰是这些 —— 它开口的时机是
「一段安静之后的场景切换」（见 director.py），而"变化"本身
在单帧里是不存在的。

Gemini 直接吃视频：`{"type": "video", "data": <base64>, "mime_type": "video/mp4"}`。
8 秒 640 宽的片段约 1–2 MB，**走 inline 不走 Files API** ——
官方给 inline 的适用区间是「<100MB、<1 分钟、一次性输入」，正好是我们。
Files API 要 upload → 轮询 ACTIVE → 再请求，三个来回，
对共影这种「她问了要马上答」的场景纯属浪费。

## ⚠️ 花销必须记下来，不能靠猜

2026-09-04 决定先跑一个月按量付费，看真实账单。所以每一次调用都往
`vision-usage.jsonl` 追一行，记 token 用量和当时算出的钱。

**记的是 API 回的 `usage` 原文，不是我以为的字段名。** Interactions API
的 usage 结构（thought / tool_use 那几项）我没有实测过，猜字段名就会
悄悄记成 0 —— 而「花了 0 块」和「没记上」看起来一模一样，
一个月后翻账本才发现全是空的。所以原文照存，钱另算，算不出来就
`cost_usd=None` 并写明白原因。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time

import httpx

logger = logging.getLogger("co-watching.vision")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
#: 便宜档。视频输入和文本同价 $0.30/M，共影一个月几毛钱（2026-09-04 调研）。
#: 走谁家。`openrouter`（默认）或 `google`。
#:
#: ## 为什么默认绕一层，不直连 Google
#:
#: 2026-09-04 查实：**中国大陆不在 Google Cloud 支持结算的 66 个国家/地区里**
#: （智利、圣诞岛都在，中国不在），而且它明说「预付卡不收」「需要双重验证的
#: 借记卡不收」—— 国内的卡基本都要短信/3DS。也就是说 Gemini 直连这条路
#: 在付费这一关就走不通。
#:
#: OpenRouter 收**支付宝**，按 token 原价转发不加价（只在充值时收 5.5% 平台费），
#: 而且 nox-core 那边本来就有一把 key。绕这一层是为了能付钱，不是为了别的。
PROVIDER = os.environ.get("WATCH_VISION_PROVIDER", "openrouter")

#: 换档只改这里 —— 但记得 PRICING 也要跟着加一条，否则账本记不出钱。
#: 两家的型号 id 不一样：Google 是 `gemini-3.5-flash-lite`，
#: OpenRouter 要带厂商前缀 `google/gemini-3.5-flash-lite`。
GEMINI_MODEL = os.environ.get(
    "WATCH_GEMINI_MODEL",
    "google/gemini-3.5-flash-lite" if PROVIDER == "openrouter"
    else "gemini-3.5-flash-lite")

#: 每 1M token 的价格（美元）。来源：ai.google.dev/gemini-api/docs/pricing
#: 与 openrouter.ai/api/v1/models，2026-09-04 —— 两边同价，OpenRouter 不加价。
#:
#: ⚠️ 价格会变，而且好几个型号写着「through December 31, 2026」之后翻倍。
#: 账本里存的是**当时**算出的钱，不是每次重算 —— 改这张表不会篡改历史记录。
#:
#: ⚠️ 走 OpenRouter 时这张表只是**兜底**：它每次都回 `usage.cost`（真实扣费），
#: 那个数才是准的，优先用它。见 `_cost()`。
PRICING = {
    "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50},
    "gemini-3.1-flash-lite": {"in": 0.25, "out": 1.50},
    "gemini-3.8-flash": {"in": 0.75, "out": 3.75},
    "gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40},
}

#: 给他看多长。8 秒 ≈ 一个镜头到下一个镜头，够看清「发生了什么」。
#: 静态模式下 Gemini 按 1 fps 采样，低分辨率约 100 token/秒 —— 8 秒 ≈ 800 token。
CLIP_S = float(os.environ.get("WATCH_CLIP_S", "8"))
#: 从触发点往前退这么久开始截。触发点是「切换刚发生」，
#: 光看切换之后没有对照 —— 要带上切换**之前**那一下才知道变了什么。
CLIP_LEAD_S = float(os.environ.get("WATCH_CLIP_LEAD_S", "3"))
#: 截出来缩到这么宽。再大只是把钱烧在 Gemini 的 media_resolution 上，
#: 而共影要的是「谁在干什么」，不是读清楚背景里的报纸。
CLIP_WIDTH = 640

#: 账本。和 data/ 里其它东西放一起，跟着会话数据一块备份。
LEDGER = os.environ.get(
    "WATCH_USAGE_LEDGER", "/root/co-watching/data/vision-usage.jsonl")

CLIP_DIR = "/tmp/watching-clips"

_PROMPT = (
    "你是另一个 AI 的眼睛。这是电影里的一小段（几秒钟）。"
    "把它如实、具体地讲清楚，让一个没看见的人能凭你的描述接着聊下去。\n"
    "要求：\n"
    "1. 先说这是什么场景，再说这几秒里**发生了什么变化**——"
    "谁动了、镜头怎么动、有没有换场\n"
    "2. 画面里所有可读的文字（字幕、招牌）都要原样抄下来\n"
    "3. 说清氛围（光线、色调、情绪）\n"
    "4. 只说你真的看见的。看不清就说看不清，不要猜、不要补全\n"
)


def _env_key(name: str) -> str:
    """从 nox-core 的 .env 里取一把 key，取不到再看环境变量。

    和老的 `_vision_key()` 一个路子 —— **不新申请、不新建配置文件**，
    密钥统一放在那一份里（见 LOCAL-SECRETS.md）。
    """
    env_path = "/root/nox-core/.env"
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(name, "")


def key() -> str:
    """当前 provider 用哪把 key。空字符串 = 没配，调用方据此兜底。"""
    if PROVIDER == "openrouter":
        return _env_key("OPENROUTER_API_KEY")
    return _env_key("GEMINI_API_KEY")


def cut_clip(url: str, t: float, sid: str, referer: str = "") -> str | None:
    """从直链截 T 前后的一小段，返回 mp4 路径。失败 None。

    `-ss` 放在 `-i` **前面**（输入端 seek）：ffmpeg 直接跳到关键帧再开读，
    不用把前面几十分钟解出来扔掉。代价是落点会对齐到最近的关键帧
    （偏差通常 <2 秒），对「这一幕大概在讲什么」完全够用。
    """
    os.makedirs(CLIP_DIR, exist_ok=True)
    start = max(0.0, t - CLIP_LEAD_S)
    out = os.path.join(CLIP_DIR, f"{sid}_{int(t)}.mp4")
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < 600:
        return out  # 10 分钟内复用（她可能连着问同一幕）

    cmd = ["ffmpeg", "-y", "-ss", str(start)]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n"]
    cmd += ["-i", url, "-t", str(CLIP_S),
            # 只要画面。音轨在 B站是分开的一条流，这儿拿不到，
            # 硬编 -an 免得 ffmpeg 为了找音频多等一轮
            "-an",
            "-vf", f"scale={CLIP_WIDTH}:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            # moov 挪到文件头 —— 不然 base64 出去 Gemini 得先下完整个文件才知道怎么解
            "-movflags", "+faststart", out]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        logger.warning("截片段失败 %s@%s: %s", sid, t, (r.stderr or "")[-300:])
    except Exception as exc:  # noqa: BLE001
        logger.warning("截片段异常 %s@%s: %s", sid, t, exc)
    return None


def still_from_clip(clip_path: str, out_jpg: str) -> bool:
    """从**已经截好的片段**里抠一张图，给前端显示用。

    前端的「问这一幕」会把画面显示出来（`/api/framefile/...`），
    换成看片段之后那张图还得有。从本地的 mp4 里抠，
    **不再去网上拉第二次** —— 直链是有时效的，多拉一次就多一次过期的机会。
    """
    cmd = ["ffmpeg", "-y", "-i", clip_path, "-frames:v", "1",
           "-q:v", "4", out_jpg]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("从片段抠图失败 %s: %s", clip_path, exc)
        return False


def _cost(model: str, usage: dict) -> tuple[float | None, str]:
    """按 PRICING 把 usage 折成钱。算不出来就说清楚为什么，**不返回 0**。

    「花了 0 块」和「没算出来」必须能分辨 —— 前者是省钱，后者是账本坏了。
    """
    if not isinstance(usage, dict) or not usage:
        return None, "这次没拿到 usage，钱没算"

    # OpenRouter 每次都回 `cost` —— **真实扣了多少**，不是按价目表推的。
    # 有它就用它：型号改了、价格调了、缓存命中了，这个数都还是对的。
    real = usage.get("cost")
    if isinstance(real, (int, float)):
        return round(float(real), 8), ""

    # 直连 Google 没有 cost 字段，只能自己按价目表算
    rate = PRICING.get(model) or PRICING.get(model.split("/", 1)[-1])
    if not rate:
        return None, f"PRICING 里没有 {model} 的价，钱没算"

    # Interactions API 的字段名我没实测过，把见过的几种写法都认一遍。
    # 一个都没认出来就如实说，不要默默当 0。
    def pick(*names):
        for n in names:
            v = usage.get(n)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    tin = pick("input_tokens", "prompt_tokens", "prompt_token_count",
               "total_input_tokens", "promptTokenCount", "inputTokens")
    tout = pick("output_tokens", "completion_tokens", "candidates_token_count",
                "total_output_tokens", "candidatesTokenCount", "outputTokens")
    # 思考和工具调用的 token 也是要付钱的（agentic 模式下是大头），
    # 能认出来就并进 output 一起算
    extra = 0.0
    for n in ("total_thought_tokens", "thoughts_token_count", "totalThoughtTokens",
              "total_tool_use_tokens", "totalToolUseTokens"):
        v = usage.get(n)
        if isinstance(v, (int, float)):
            extra += float(v)

    if tin is None and tout is None:
        return None, f"usage 里没认出 token 字段（拿到的键：{sorted(usage)[:8]}），钱没算"

    cost = ((tin or 0.0) * rate["in"] + ((tout or 0.0) + extra) * rate["out"]) / 1_000_000
    return round(cost, 8), ""


def _log(kind: str, model: str, usage: dict, cost: float | None,
         why: str, seconds: float, note: str = "") -> None:
    """往账本追一行。**写失败不许影响看片** —— 记账坏了是记账的事。"""
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": kind,            # clip | image
        "model": model,
        "usage": usage,          # 原文照存，见模块顶部那段
        "cost_usd": cost,
        "cost_note": why,
        "latency_s": round(seconds, 2),
        "note": note,
    }
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("账本写不进去（不影响看片）：%s", exc)


def _or_part(part: dict) -> dict:
    """把中立的媒体块翻成 OpenRouter（OpenAI 格式）的内容块。

    两家的名字不一样：Google 叫 `{"type":"video","data":...}`，
    OpenAI 那套叫 `{"type":"video_url","video_url":{"url":"data:..."}}`。
    """
    mime = part.get("mime_type") or ""
    data_url = f"data:{mime};base64,{part.get('data')}"
    if part.get("type") == "video":
        return {"type": "video_url", "video_url": {"url": data_url}}
    return {"type": "image_url", "image_url": {"url": data_url}}


def _text_of(data: dict) -> str:
    """从回包里把正文抠出来。两家三种形状，都试一遍。

    认不出来就返回空串，交给上面那层报「他这次没说出话来」——
    **不要在这儿静默返回一个空字符串当正常结果。**
    """
    # OpenRouter / OpenAI: choices[0].message.content
    for ch in data.get("choices") or []:
        msg = (ch or {}).get("message") or {}
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, list):  # 有的模型回的是内容块数组
            joined = "\n".join(x.get("text", "") for x in c
                               if isinstance(x, dict)).strip()
            if joined:
                return joined
    # Google Interactions: output_text，或者从 steps 里拼
    text = (data.get("output_text") or "").strip()
    if text:
        return text
    chunks = []
    for step in data.get("steps") or []:
        for c in step.get("content") or []:
            if isinstance(c, dict) and c.get("text"):
                chunks.append(c["text"])
    return "\n".join(chunks).strip()


def _ask(parts: list[dict], kind: str) -> tuple[str | None, str]:
    """发一次请求。返回 `(描述, 说不出来的原因)`。

    ⚠️ **描述为空时原因一定说得出来。** 「画面没什么好说的」和
    「这次没看成」是两件事 —— 后者必须让他知道自己是瞎的，
    否则他会装作看见了（2026-08-22 栽过一次，见 app.py 的 VISION_MAX_TOKENS）。
    """
    k = key()
    if not k:
        return None, ("没配 OPENROUTER_API_KEY" if PROVIDER == "openrouter"
                      else "没配 GEMINI_API_KEY")

    if PROVIDER == "openrouter":
        # OpenAI 那套 chat/completions。视频用 `video_url` 内容块，
        # 值是 data URL —— 官方明说：走 AI Studio 的 Gemini 只认 YouTube 链接，
        # 本地文件必须 base64。我们截出来的片段正好是本地文件。
        content = [_or_part(p) for p in parts]
        content.append({"type": "text", "text": _PROMPT})
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {k}",
                   "Content-Type": "application/json",
                   # 让 OpenRouter 的用量页能按应用分开看，对账时好认
                   "HTTP-Referer": "https://noxtang.com",
                   "X-Title": "Caelum co-watching"}
        body = {"model": GEMINI_MODEL,
                "messages": [{"role": "user", "content": content}]}
    else:
        url = f"{GEMINI_BASE}/interactions"
        headers = {"x-goog-api-key": k, "Content-Type": "application/json"}
        body = {"model": GEMINI_MODEL,
                "input": parts + [{"type": "text", "text": _PROMPT}]}

    t0 = time.time()
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=120)
    except Exception as exc:  # noqa: BLE001
        _log(kind, GEMINI_MODEL, {}, None, "请求就没发出去",
             time.time() - t0, note=str(exc)[:200])
        return None, f"这次没看成：{exc}"

    took = time.time() - t0
    if r.status_code != 200:
        why = f"{PROVIDER} 回了 {r.status_code}: {r.text[:200]}"
        _log(kind, GEMINI_MODEL, {}, None, "非 200，没有 usage", took, note=why)
        logger.warning(why)
        return None, why

    data = r.json()
    usage = data.get("usage") or data.get("usage_metadata") or {}
    cost, why = _cost(GEMINI_MODEL, usage)

    text = _text_of(data)

    if not text:
        _log(kind, GEMINI_MODEL, usage, cost, why, took, note="200 但正文是空的")
        # 200 但没正文 —— 多半是被安全策略挡了，或者 max_tokens 被思考吃光
        return None, f"他这次没说出话来（200 但正文为空，usage={usage}）"

    _log(kind, GEMINI_MODEL, usage, cost, why, took)
    return text, ""


def describe_clip(url: str, t: float, sid: str, referer: str = "") -> tuple[str | None, str]:
    """看 T 时刻前后的一小段。返回 `(描述, 说不出来的原因)`。"""
    path = cut_clip(url, t, sid, referer)
    if not path:
        return None, "这一段没截出来"
    return describe_clip_file(path)


def describe_clip_file(path: str) -> tuple[str | None, str]:
    """看一个**已经截好的**片段。调用方自己截过就走这个，别再截一遍。"""
    size = os.path.getsize(path)
    # inline 的上限是「请求体 <100MB」，base64 会胀 4/3。
    # 8 秒 640 宽正常一两 MB，到 20MB 说明源码率高得离谱，退回去别硬发
    if size > 20 * 1024 * 1024:
        return None, f"这一段太大了（{size // 1024 // 1024}MB），没发"
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    return _ask([{"type": "video", "data": b64, "mime_type": "video/mp4"}], "clip")


def describe_image(path: str) -> tuple[str | None, str]:
    """看一张图（本地片子那条路：画面由她浏览器 canvas 抓上来）。"""
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    return _ask([{"type": "image", "data": b64, "mime_type": "image/jpeg"}], "image")


def summary(since: str = "") -> dict:
    """把账本汇总一下 —— 「这个月看电影花了多少」。

    `since`: `YYYY-MM` 或 `YYYY-MM-DD` 前缀，空则全部。
    """
    total, calls, unpriced, by_kind = 0.0, 0, 0, {}
    if not os.path.exists(LEDGER):
        return {"calls": 0, "cost_usd": 0.0, "unpriced": 0, "by_kind": {},
                "note": "账本还没有任何记录"}
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and not (row.get("at") or "").startswith(since):
            continue
        calls += 1
        k = row.get("kind") or "?"
        by_kind[k] = by_kind.get(k, 0) + 1
        c = row.get("cost_usd")
        if isinstance(c, (int, float)):
            total += c
        else:
            unpriced += 1
    return {"calls": calls, "cost_usd": round(total, 6), "unpriced": unpriced,
            "by_kind": by_kind,
            # 有没算出钱的就说出来，别让人以为总额是全的
            "note": (f"{unpriced} 次没算出钱（见账本 cost_note）" if unpriced
                     else "")}
