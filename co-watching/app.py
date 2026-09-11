# -*- coding: utf-8 -*-
"""
co-watching —— 共影播放层。**双模式**。

    ① 代理拉流（默认）  /api/import + /api/stream
       yt-dlp 解析 → 选 1080p → 流式转发给前端自己的 <video>。
       时间戳、字幕、抽帧全都在我们手里 —— 「问这一幕」只有这条路走得通。
       B站走 cookies（/root/watch/cookies.txt），YouTube 直连。

    ② iframe 兜底      /api/episode
       番剧（bangumi）在东京这台 VPS 上有地域限制，拉不到流。
       但 B站官方播放器的 iframe 跑在**她自己的浏览器**里、用她的 IP，能看。
       代价：iframe 不暴露播放状态，他不知道她看到第几秒 ——
       所以那一侧靠她手动报时间（前端 Movies.jsx 的「告诉他」）。

⚠️ 命名：项目/服务/目录叫 **co-watching**（对齐 co-reading），
但对外 URL 保持 `/watch/<token>`、Core 侧工具保持 `watching_*` ——
和共读一样（目录 co-reading，URL /api/reading/*，工具 reading_*）。
改 URL 会同时打断 Caddy 路由和前端的 VITE_WATCH_URL，收益为零。
"""
import os
import re
import json
import time
import uuid
import logging
import subprocess

import yt_dlp
import httpx

import director
import observer
import vision
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("co-watching")

app = FastAPI()

# 前端（Caelum OS dev / Electron / 手机 App）跨域访问 co-watching：
# 不放开 CORS 的话浏览器 OPTIONS 预检直接 405，import/stream 全被拦
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

COOKIES = "/root/watch/cookies.txt"
DATA_DIR = "/root/co-watching/data"


@app.get("/health")
def health():
    """部署健康检查（也顺手当一次数据目录自检）。

    2026-09-11 铺 release 布局时加的。`deploy-remote.sh` 要求它返回 200，
    否则**自动回滚**。

    ⚠️ 这里查的是**数据目录在不在**，不是「服务活着吗」—— 因为服务器活着
    恰恰是那个陷阱最难查的地方：数据路径一旦莫名其妙指到新目录，
    服务照常起、照常 200，只是数据看起来全没了。
    co-watching 现在所有数据路径都是绝对路径（DATA_DIR / COOKIES / LEDGER /
    /tmp/watching-*），所以这条正常情况下永远绿；它防的是**以后**有人加一个
    相对路径。
    """
    ok = os.path.isdir(DATA_DIR)
    return JSONResponse(
        {"ok": ok, "data_dir": DATA_DIR, "data_dir_exists": ok},
        status_code=200 if ok else 503,
    )
# 字幕语言优先级：B站/YouTube 中文优先，fallback 英文
SUB_LANGS = ["zh-Hans", "zh-CN", "zh", "ai-zh", "en"]
# 直链有时效（yt-dlp 拿到的流地址几分钟过期），缓存 90 秒后重新解析
DIRECT_URL_TTL = 90
# 格式选择：1080p mp4 优先，退化到任意 ≤1080p
FORMAT = (
    "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
    "bv*[height<=1080]+ba/b[height<=1080]/b"
)
# 最多同时多少个会话（VPS 小，防止内存/连接爆掉）
MAX_SESSIONS = 8

sessions: dict[str, dict] = {}


def _opts() -> dict:
    o = {
        "quiet": True,
        "no_warnings": True,
        "format": FORMAT,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
    }
    if os.path.exists(COOKIES):
        o["cookiefile"] = COOKIES
    return o


def _extract(url: str) -> dict:
    with yt_dlp.YoutubeDL(_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    return info


# ------------------------------------------------------------------ 字幕

def _ts(s: str) -> float:
    """时间戳 '12:34.567' / '1:02:03.400' → 秒"""
    parts = s.replace(",", ".").split(":")
    sec = float(parts[-1])
    if len(parts) > 1:
        sec += int(parts[-2]) * 60
    if len(parts) > 2:
        sec += int(parts[-3]) * 3600
    return round(sec, 2)


def _parse_vtt(path: str) -> list[dict]:
    """WEBVTT → [{start, end, text}]"""
    items: list[dict] = []
    cur: dict | None = None
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if "-->" in line:
                m = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", line)
                if m:
                    if cur:
                        items.append(cur)
                    cur = {"start": _ts(m.group(1)), "end": _ts(m.group(2)), "text": ""}
            elif cur is not None and line and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
                cur["text"] = (cur["text"] + " " + line).strip()
    if cur:
        items.append(cur)
    return [i for i in items if i["text"]]


def _parse_srt(text: str) -> list[dict]:
    items: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        if "-->" in lines[1]:
            m = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", lines[1])
            if m:
                items.append({
                    "start": _ts(m.group(1)),
                    "end": _ts(m.group(2)),
                    "text": " ".join(lines[2:]).strip(),
                })
    return [i for i in items if i["text"]]


def _fetch_subtitles(sid: str, url: str) -> dict | None:
    """抓字幕并解析成 subtitle.json。失败返回 None，不影响视频导入。

    路径：extract_info 拿 automatic_captions/subtitles 的**直链**，httpx 手动下载
    （yt-dlp 自己的字幕下载流程在 YouTube 上会撞 429 限流；直链 + 退避重试更稳）。
    """
    d = os.path.join(DATA_DIR, sid)
    try:
        info = _extract(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("字幕解析拿 info 失败 %s: %s", sid, exc)
        return None

    caps = info.get("automatic_captions") or {}
    subs = info.get("subtitles") or {}
    chosen = None
    for lang in SUB_LANGS:
        if lang in caps:
            chosen = (lang, caps[lang]); break
        if lang in subs:
            chosen = (lang, subs[lang]); break
    if not chosen:
        for src in (caps, subs):
            if src:
                lang = next(iter(src)); chosen = (lang, src[lang]); break
    if not chosen:
        logger.info("无字幕 %s", sid)
        return None

    lang, variants = chosen
    if not variants:
        return None
    direct = variants[0].get("url")
    if not direct:
        return None

    # 直链下载，429 退避重试 2 次
    text = None
    for attempt in range(3):
        try:
            resp = httpx.get(direct, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                text = resp.content.decode("utf-8-sig", errors="replace")
                break
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            logger.warning("字幕直链 HTTP %d for %s", resp.status_code, sid)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("字幕直链请求异常 %s: %s", sid, exc)
            time.sleep(1)
    if text is None:
        logger.warning("字幕下载失败 %s（重试后仍 429/失败）", sid)
        return None

    try:
        if "WEBVTT" in text[:100] or ".vtt" in direct:
            items = _parse_vtt_text(text)
        else:
            items = _parse_srt(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("字幕解析失败 %s: %s", sid, exc)
        return None

    if not items:
        return None
    os.makedirs(d, exist_ok=True)
    out = {"lang": lang, "count": len(items), "items": items}
    with open(os.path.join(d, "subtitle.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    logger.info("字幕就绪 %s: %d 条 (%s)", sid, len(items), lang)
    return out


def _parse_vtt_text(text: str) -> list[dict]:
    """VTT 文本 → [{start, end, text}]（不依赖文件）"""
    items: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if "-->" in line:
            m = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", line)
            if m:
                if cur:
                    items.append(cur)
                cur = {"start": _ts(m.group(1)), "end": _ts(m.group(2)), "text": ""}
        elif cur is not None and line and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            cur["text"] = (cur["text"] + " " + line).strip()
    if cur:
        items.append(cur)
    return [i for i in items if i["text"]]


@app.post("/api/import")
def import_video(body: dict):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    if len(sessions) >= MAX_SESSIONS:
        # 满员时丢最老的会话
        oldest = min(sessions, key=lambda k: sessions[k]["created"])
        sessions.pop(oldest, None)
        logger.info("会话满员，丢弃最老 %s", oldest)

    sid = uuid.uuid4().hex[:12]
    try:
        info = _extract(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("import 失败 %s: %s", url, exc)
        raise HTTPException(422, f"解析失败: {exc}")

    # ⚠️ 这里**不选格式**。直链几分钟就过期，import 时挑好没有意义 ——
    # 真正的挑选在 `_fresh_direct_url()`，每次 /api/stream 现挑现用。
    # 这儿只把整个 info 存下来。
    sessions[sid] = {
        "id": sid,
        # 原始链接。**视觉分析按视频建身份用它**（sid 每次导入都是新的，
        # 而同一部片子的分析结果应该复用）
        "source_url": url,
        "title": info.get("title") or "未命名",
        "duration": info.get("duration"),
        "extracted_at": time.time(),
        "info": info,
        "created": time.time(),
    }
    logger.info("import 完成 %s | %s", sid, sessions[sid]["title"])

    # 抓字幕（后台拿得到就存，拿不到不影响播放）
    try:
        _fetch_subtitles(sid, url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("字幕抓取异常 %s: %s", sid, exc)

    return {
        "session_id": sid,
        "title": sessions[sid]["title"],
        "duration": sessions[sid]["duration"],
        "stream_url": f"/api/stream/{sid}",
        "subtitles_url": f"/api/subtitles/{sid}",
        # B站是 DASH 分轨的：前端要另挂一个 <audio> 对时，否则没声音
        "separate_audio": _has_separate_audio(sid),
        "audio_url": f"/api/stream/{sid}?track=audio",
    }


@app.get("/api/subtitles/{sid}")
def subtitles(sid: str):
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    p = os.path.join(DATA_DIR, sid, "subtitle.json")
    if not os.path.exists(p):
        raise HTTPException(404, "没有字幕")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ 帧观察

FRAME_DIR = "/tmp/watching-frames"
# vision 模型与 key（复用 nox-core 的 DeepSeek key；模型就是共影的「帧的眼睛」）
VISION_MODEL = "deepseek-v4-flash-vision-exp"
VISION_BASE = "https://api.deepseek.com/v1"

#: 眼睛用谁。`gemini`（默认）= 看**一段**，见 vision.py；`deepseek` = 看**一帧**，
#: 就是下面那套老的。
#:
#: ⚠️ **Gemini 出错时不偷偷退回 DeepSeek。** 2026-09-04 起在跑「一个月按量付费
#: 到底花多少」的实测，静默兜底会让账本少记而她以为那就是全部开销。
#: 要换回老路子就把这个环境变量显式改掉。
VISION_BACKEND = os.environ.get("WATCH_VISION_BACKEND", "gemini")

_warned_no_gemini = False


def _backend() -> str:
    """这一刻实际用哪个后端。

    ⚠️ **配了 gemini 但没有 key 时留在 deepseek，不要让他变瞎。**
    代码先上线、key 后补是常态（2026-09-04 就是这个顺序）——
    这中间的窗口里共影得照常能看，key 一落地下次调用自动切过去，
    不用改代码也不用重启。

    ⚠️ 这**只在「压根没配 key」时兜底**。key 配了之后 Gemini 报错就是报错，
    不偷偷退回 DeepSeek —— 那会让账本少记，而她正拿这本账估一个月的开销。
    """
    global _warned_no_gemini
    if VISION_BACKEND != "gemini":
        return VISION_BACKEND
    if vision.key():
        return "gemini"
    if not _warned_no_gemini:
        need = ("OPENROUTER_API_KEY" if vision.PROVIDER == "openrouter"
                else "GEMINI_API_KEY")
        logger.warning("WATCH_VISION_BACKEND=gemini（provider=%s）但没有 %s，"
                       "先留在 deepseek。往 /root/nox-core/.env 里加 %s= "
                       "就会自动切过去", vision.PROVIDER, need, need)
        _warned_no_gemini = True
    return "deepseek"

_VISION_PROMPT = (
    "你是另一个 AI 的眼睛。把这张电影画面如实、具体地讲清楚，让一个看不见它的人"
    "能凭你的描述接着聊下去。\n"
    "要求：\n"
    "1. 用连贯的话讲，先说这是什么场景，再说画面里有什么、谁在场\n"
    "2. 画面里所有可读的文字（字幕、招牌、弹幕）都要原样抄下来\n"
    "3. 说清氛围（光线、色调、情绪）\n"
    "4. 只说你真的看见的。看不清就说看不清，不要猜、不要补全\n"
)


def _vision_key() -> str:
    """复用 nox-core 的 DEEPSEEK_API_KEY（不新申请）。"""
    env_path = "/root/nox-core/.env"
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DEEPSEEK_API_KEY", "")


def _extract_frame(url: str, t: float, sid: str) -> str | None:
    """ffmpeg 从在线直链抽一帧（T 时刻），返回 jpg 路径。失败 None。"""
    os.makedirs(FRAME_DIR, exist_ok=True)
    out = os.path.join(FRAME_DIR, f"{sid}_{int(t)}.jpg")
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < 600:
        return out  # 10 分钟内缓存
    # B站防盗链要 Referer
    headers = "Referer: https://www.bilibili.com/\r\nUser-Agent: Mozilla/5.0\r\n"
    cmd = ["ffmpeg", "-y", "-ss", str(max(0, t)), "-headers", headers,
           "-i", url, "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", out]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        logger.warning("抽帧失败: %s", r.stderr[-200:])
    except Exception as exc:  # noqa: BLE001
        logger.warning("抽帧异常 %s@%s: %s", sid, t, exc)
    return None


#: 看一张图给多少 token。
#:
#: ⚠️ **这个模型会「思考」，而 reasoning 和 content 共用 max_tokens。**
#: 2026-08-22 实测同一张图连打四次：
#:
#:     max_tokens=600   finish=length   reasoning=600    content=0     ← 空的
#:     max_tokens=600   finish=stop     reasoning=295    content=365
#:     max_tokens=1500  finish=length   reasoning=1325   content=274
#:     max_tokens=1500  finish=stop     reasoning=945    content=357
#:
#: 思考长度在 295~1325 之间乱跳。原来的 600 大约**一半的概率**被思考吃光，
#: content 变成空 —— 表现是她问「这一幕」时他有一半是瞎的。给足额度。
VISION_MAX_TOKENS = 2400


def _describe_frame(frame_path: str) -> tuple[str | None, str]:
    """vision 模型看图。返回 `(描述, 说不出来的原因)`。

    ⚠️ **不许只返回 None。** 「没描述出来」有三种完全不同的原因：
    没配 key（配置问题）、调用炸了（故障）、思考吃光额度（额度问题）。
    混成一个 None，调用方只能当「画面没什么可说的」，
    于是他会**装作看见了**去回答 —— 这正是 guard 那条「不许编」要防的事。
    """
    key = _vision_key()
    if not key:
        return None, "没配 vision 的 key"
    try:
        import base64
        from openai import OpenAI
        b64 = base64.b64encode(open(frame_path, "rb").read()).decode()
        client = OpenAI(api_key=key, base_url=VISION_BASE, timeout=90)
        msg = [{"role": "user", "content": [
            {"type": "text", "text": _VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]
        try:
            r = client.chat.completions.create(
                model=VISION_MODEL, max_tokens=VISION_MAX_TOKENS, messages=msg,
                # 🔴 **关掉思考。** 实测这一项把看图从 10~15 秒压到 2~3 秒。
                #
                # 2026-08-22 量的：思考占输出 token 的 78%，而描述质量
                # 一模一样 —— 两版都准确抄下了画面里的字幕和水印，
                # 不思考版还把可读文字分条列了出来。
                #
                # ⚠️ 别用 minimal / low：实测**比默认还慢**
                #     默认 6.5s（思考 363）· minimal 12.1s（1058）· low 13.9s（1156）
                #     none 2.4s（0）
                # 也别指望在 prompt 里写「不要思考过程」—— 试过，思考反而涨到 1557
                reasoning_effort="none",
            )
        except Exception:  # noqa: BLE001
            # 哪天这个参数没了也别整条链挂掉，退回慢的那条
            logger.warning("reasoning_effort=none 不被接受，退回默认（会慢 3-5 倍）")
            r = client.chat.completions.create(
                model=VISION_MODEL, max_tokens=VISION_MAX_TOKENS, messages=msg)
        choice = r.choices[0]
        text = (choice.message.content or "").strip()
        if text:
            return text, ""
        # 200 但没内容。**必须说清楚为什么** —— 这是最容易被当成
        # 「画面没什么好说的」的一种失败
        why = ("看图时思考占满了额度（finish=length），没写出描述"
               if choice.finish_reason == "length" else
               f"看图回了空内容（finish={choice.finish_reason}）")
        logger.warning("%s：%s", frame_path, why)
        return None, why
    except Exception as exc:  # noqa: BLE001
        logger.warning("看图失败 %s: %s", frame_path, exc)
        return None, f"看图失败：{type(exc).__name__}"


def _look_at(sid: str, t: float) -> tuple[str | None, str, bool]:
    """让他看 T 时刻。返回 `(描述, 说不出来的原因, 有没有配图)`。

    「问这一幕」和「主动点评」共用这条 —— 两边都要花钱，
    走同一条才保证记在同一本账上（`/api/vision/usage`）。
    """
    url = _fresh_direct_url(sid)
    if _backend() != "gemini":
        path = _extract_frame(url, t, sid)
        if not path:
            return None, "这一帧没抽出来", False
        desc, note = _describe_frame(path)
        return desc, note, True

    # 看一段。截出来的 mp4 顺手抠一张图给前端显示 ——
    # 不为了那张图再去网上拉一次，直链多拉一次就多一次过期的机会
    src = sessions[sid].get("source_url") or ""
    clip = vision.cut_clip(url, t, sid, _referer(src))
    if not clip:
        return None, "这一段没截出来", False
    os.makedirs(FRAME_DIR, exist_ok=True)
    still = os.path.join(FRAME_DIR, f"{sid}_{int(t)}.jpg")
    has_still = vision.still_from_clip(clip, still)
    desc, note = vision.describe_clip_file(clip)
    return desc, note, has_still


@app.get("/api/frame/{sid}")
def frame(sid: str, t: float = 0):
    """问这一幕的视觉证据：抽 T 时刻的帧 + vision 描述。

    返回 `{frame_url, description, note}`。

    ⚠️ **`description` 为空时 `note` 一定说得出原因。**
    「画面没什么好说的」和「这次没看成」是两件事 —— 前者可以照常聊，
    后者必须让他知道自己是瞎的，否则他会装作看见了（2026-08-22 就栽在这儿：
    思考吃光额度导致一半的请求静默返回空，谁也不知道）。
    """
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    desc, note, has_still = _look_at(sid, t)
    return {"frame_url": (f"/api/framefile/{sid}/{int(t)}" if has_still else None),
            "description": desc, "note": note}


@app.post("/api/vision/frame")
def vision_frame(body: dict):
    """看一张**前端传上来的**图（本地视频用）。

    本地片子在她自己机器上，服务端够不到 —— 所以画面由浏览器 canvas 抓，
    只把这一帧（约 50KB）传上来。**比传整部片子省三个数量级。**

    body: `{"image_b64": "<不带 data: 前缀的 base64>"}`
    """
    b64 = (body or {}).get("image_b64") or ""
    if "," in b64:                      # 前端可能直接把 dataURL 丢过来
        b64 = b64.split(",", 1)[1]
    if not b64:
        raise HTTPException(400, "image_b64 required")
    # 一帧几十 KB；超过 6MB 说明前端把原始分辨率整帧发上来了
    if len(b64) > 8_000_000:
        raise HTTPException(413, "这一帧太大了，前端该先缩一下")

    import base64 as _b64
    os.makedirs(FRAME_DIR, exist_ok=True)
    path = os.path.join(FRAME_DIR, f"local_{uuid.uuid4().hex[:10]}.jpg")
    try:
        with open(path, "wb") as f:
            f.write(_b64.b64decode(b64))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"这不是一张能解的图：{exc}") from exc

    try:
        # 本地片子这条路画面是她浏览器 canvas 抓的**一帧**，没有片段可看 ——
        # 要看一段得前端先录一小截再传，那是另一件事。这儿只换后端，
        # 让两条路的花销记在同一本账上
        if _backend() == "gemini":
            desc, note = vision.describe_image(path)
        else:
            desc, note = _describe_frame(path)
        return {"description": desc, "note": note}
    finally:
        # 本地视频的帧**不留** —— 它是她硬盘里的片子，没有理由在服务器上过夜
        try:
            os.remove(path)
        except OSError:
            pass


@app.get("/api/vision/usage")
def vision_usage(since: str = ""):
    """这段时间看电影花了多少。`?since=2026-09` 按月，留空是全部。

    2026-09-04 起跑按量付费实测用的。`unpriced` 不是 0 就说明有几次
    **没算出钱**（不是没花钱）—— 总额是偏低的，去账本看 `cost_note`。
    """
    return {"backend": _backend(), "model": vision.GEMINI_MODEL,
            **vision.summary(since)}


@app.get("/api/framefile/{sid}/{ts}")
def framefile(sid: str, ts: int):
    """返回抽好的帧图（前端可显示）。"""
    p = os.path.join(FRAME_DIR, f"{sid}_{ts}.jpg")
    if not os.path.exists(p):
        raise HTTPException(404, "帧不存在")
    return FileResponse(p, media_type="image/jpeg")


# ------------------------------------------------------------------ 番剧 iframe

@app.post("/api/episode")
def episode(body: dict):
    """番剧链接 → aid/cid → B站官方嵌入播放器 URL。

    番剧（bangumi）在东京服务器上被地域限制拉不了流，但 B站官方播放器的
    iframe 是跑在**用户浏览器**里的 —— 用用户自己的 IP 播，能看番剧。
    代价是没有时间戳同步（iframe 不暴露播放状态），问这一幕只能靠用户自己
    报告时间点。
    """
    url = (body.get("url") or "").strip()
    if "bangumi" not in url:
        raise HTTPException(400, "不是番剧链接")
    m = re.search(r"bangumi/play/(ep|ss)(\d+)", url)
    if not m:
        raise HTTPException(400, "认不出番剧集号")

    kind, num = m.group(1), m.group(2)
    # ⚠️ **ss 是季号，ep 才是集号，查询参数不一样。**
    #
    # 2026-08-22 糖糖贴了一条 `bangumi/play/ss211802`，两条路一起挂：
    # 拉流被地域限制（预期，该走 iframe 兜底），而兜底这条把季号当集号查，
    # B站回 -404「啥都木有」→ 422。她看到的就是那个 422。
    #     ep_id=211802     → -404
    #     season_id=211802 → 0，《挽救计划》
    param = "ep_id" if kind == "ep" else "season_id"

    try:
        resp = httpx.get(
            f"https://api.bilibili.com/pgc/view/web/season?{param}={num}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
            timeout=20,
        )
        d = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("番剧 API 失败 %s: %s", url, exc)
        raise HTTPException(502, "B站番剧接口没回")

    if d.get("code") != 0:
        # 带上用的哪个参数 —— 下次再挂能一眼看出是不是又认错了号
        raise HTTPException(
            422, f"{d.get('message') or '番剧接口错误'}（{param}={num}）")

    data = d["result"]
    eps = data.get("episodes") or []
    # ss 链接指的是整季，没有具体哪一集 —— 从第一集开始（电影只有一集）
    ep = (next((e for e in eps if str(e.get("id")) == num), None) if kind == "ep"
          else None) or (eps[0] if eps else None)
    if not ep:
        raise HTTPException(422, "没找到这一集")
    aid, cid = ep.get("aid"), ep.get("cid")
    if not aid or not cid:
        raise HTTPException(422, "这集没有可播的播放信息")

    # ⚠️ **报事实，不做预测。**
    #
    # 2026-08-22 糖糖贴《挽救计划》，iframe 里出现 B站的「无法播放」。
    # 查到 `badge=会员` + `大会员专享` + `area_limit`，第一反应是
    # 「据此判断能不能播」—— 然后发现她 8-21 看过的《新神榜：杨戬》
    # **三个字段一模一样**。这个信号分不出两者，拿它当判断等于逢番剧喊狼来了。
    #
    # 假的「播不了」比不提示更糟：她会因此不去试一部本来能看的片子。
    #
    # 所以只把知道的事实带出去，由前端轻描淡写地提一句，不下结论。
    # （真正的原因多半是这个 iframe 跑在 Electron 里、**不带她的 B站登录** ——
    # 那要做「在应用里登录 B站」才能解，是另一件事。）
    rights = data.get("rights") or {}
    tip = ((data.get("payment") or {}).get("tip") or "").strip()
    badge = (ep.get("badge") or "").strip()
    notes = []
    if badge:
        notes.append(badge)
    if tip:
        notes.append(tip)
    if rights.get("area_limit"):
        notes.append("有地域限制")

    embed = (
        f"https://player.bilibili.com/player.html?aid={aid}&cid={cid}"
        "&high_quality=1&danmaku=1"
    )
    logger.info("番剧解析 %s → aid=%s cid=%s", m.group(2), aid, cid)
    return {
        "title": data.get("title") or "",
        "episode": ep.get("long_title") or ep.get("title") or "",
        "desc": (data.get("evaluate") or "").strip(),
        "embed_url": embed,
        # B站标了什么就带什么出去。**这是事实不是结论** ——
        # 播不播得了只有她那边真放一下才知道（见上面那段）
        "notes": notes,
    }


def _fresh_direct_url(sid: str, track: str = "video") -> str:
    """拿直链。`track`: video | audio。

    ## ⚠️ B站是 DASH，音视频**分轨**，而且没有合一的格式

    2026-08-22 实测那条视频：15 个格式里**音视频都有的是 0 个**，
    `requested_formats` 是两段 —— 1080p 的纯视频轨 + 一条 m4a 音频轨。

    所以这里只能二选一，而这个函数原来一律返回视频轨 ——
    **拉流从 8-21 上线起就一直是无声的**，谁也没发现：
    冒烟测试只验「有字节流出来」，字节确实一直在流。

    合流的活交给前端（两个元素对时），不在这儿用 ffmpeg 合 ——
    合了就没有 byte-range，进度条拖不动，而且要占这台 2 核机器的 CPU。
    """
    s = sessions[sid]
    info = s.get("info")
    # requested_formats: 分轨时是两段（视频 + 音频）
    fmts = info.get("requested_formats") or []
    if not fmts:
        # 单文件（本来就合好的）就看 formats
        fmts = [f for f in (info.get("formats") or []) if f.get("url")]
    if not fmts:
        raise HTTPException(422, "没有可播的格式")

    def has_video(f):
        return (f.get("vcodec") or "none").lower() != "none"

    def has_audio(f):
        return (f.get("acodec") or "none").lower() != "none"

    if track == "audio":
        # 纯音频轨优先；没有分轨（合一的格式）时退回那个合一的
        picked = [f for f in fmts if has_audio(f) and not has_video(f)] \
            or [f for f in fmts if has_audio(f)]
        if not picked:
            raise HTTPException(404, "这个源没有单独的音频轨")
    else:
        picked = [f for f in fmts if has_video(f)] or fmts

    url = picked[0].get("url")
    if not url:
        raise HTTPException(422, "格式缺直链")
    return url


def _has_separate_audio(sid: str) -> bool:
    """这个源是不是分轨的（前端据此决定要不要挂第二个元素）。"""
    info = sessions[sid].get("info") or {}
    fmts = info.get("requested_formats") or info.get("formats") or []
    return any((f.get("acodec") or "none").lower() != "none"
               and (f.get("vcodec") or "none").lower() == "none"
               for f in fmts)


@app.get("/api/stream/{sid}")
def stream(sid: str, request: Request, track: str = "video"):
    """代理一条轨。`?track=audio` 拿声音那条 —— B站是分轨的，见 _fresh_direct_url。"""
    if sid not in sessions:
        raise HTTPException(404, "session 不存在或已过期")
    try:
        url = _fresh_direct_url(sid, "audio" if track == "audio" else "video")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("取直链失败 %s: %s", sid, exc)
        raise HTTPException(502, f"取流失败: {exc}")

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
    }
    # 防盗链：B站 CDN 直链要 Referer，YouTube 直链也要（防 403）
    if "bilivideo" in url or "bilibili" in url:
        headers["Referer"] = "https://www.bilibili.com/"
    elif "googlevideo" in url or "youtube" in url:
        headers["Referer"] = "https://www.youtube.com/"
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    # 先打开上游流，同步拿到状态码和 Content-Range/Length，前端才能 seek
    upstream = httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=30)
    resp = upstream.__enter__()
    if resp.status_code >= 400:
        upstream.__exit__(None, None, None)
        logger.warning("上游 %d for %s", resp.status_code, sid)
        raise HTTPException(502, "上游取流失败")

    resp_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }
    cr = resp.headers.get("content-range")
    cl = resp.headers.get("content-length")
    if cr:
        resp_headers["Content-Range"] = cr
    if cl:
        resp_headers["Content-Length"] = cl

    def gen():
        try:
            yield from resp.iter_bytes(65536)
        finally:
            upstream.__exit__(None, None, None)

    return StreamingResponse(
        gen(),
        media_type="video/mp4",
        status_code=resp.status_code if resp.status_code in (200, 206) else 200,
        headers=resp_headers,
    )


# ------------------------------------------------------------------ 视觉预分析
#
# 便宜的那层（见 observer.py 开头）。**只预计算、存 JSON，不接触发器** ——
# 触发器是 P3 的活，这一步只负责把信号攒出来。


def _referer(url: str) -> str:
    return ("https://www.bilibili.com/" if "bili" in url
            else "https://www.youtube.com/")


def _analyzer_for(sid: str):
    """会话 → 分析器。**按视频建身份，不按会话** ——
    同一部片子第二次导入应该直接复用上次的结果，而 sid 每次都是新的。"""
    s = sessions[sid]
    url = s.get("source_url") or ""
    dur = int((s.get("duration") or 0) * 1000)
    if not url or dur <= 0:
        raise HTTPException(422, "这一场没有源链接或时长，分析不了")
    return observer.Analyzer(DATA_DIR, observer.media_key(url), dur), url


@app.post("/api/analyze/{sid}")
def analyze_start(sid: str):
    """开跑（后台）。已经在跑就如实说在跑，不重复起。"""
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    an, url = _analyzer_for(sid)
    an.ensure()
    started = observer.start_background(an, lambda: _fresh_direct_url(sid), _referer(url))
    return {"ok": True, "key": an.key, "started": started,
            "already_running": not started, **an.progress()}


@app.get("/api/analyze/{sid}")
def analyze_status(sid: str):
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    an, _ = _analyzer_for(sid)
    return {"ok": True, "key": an.key,
            "running": observer.is_running(an.key), **an.progress()}


@app.get("/api/observations/{sid}")
def observations(sid: str, from_ms: int = 0, to_ms: int = 0):
    """区间内的视觉事件。

    ⚠️ **只回事件，不回原始帧差分数。** 上层要的是「这里切了一刀」，
    不是 600 个浮点数（架构 14.4）。分数留在 chunk 文件里，调阈值时才用。
    """
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    an, _ = _analyzer_for(sid)
    to_ms = to_ms or an.duration_ms
    return {"ok": True, "from_ms": from_ms, "to_ms": to_ms,
            "items": an.events(from_ms, to_ms)}


# ------------------------------------------------------------------ 主动开口
#
# 「这一秒要不要说话」的判断住在这里，不去问 Core —— 架构第六节：
# 共影是强时序场景，往返裁决会损害延迟和节奏。Core 只负责**说什么**。

#: sid → 这一场的现场状态。进程内，重启就没了（重来最多多说几句）
_directors: dict[str, director.DirectorState] = {}


def _subs_of(sid: str) -> list[dict]:
    p = os.path.join(DATA_DIR, sid, "subtitle.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("items") or []
    except (OSError, ValueError):
        return []


@app.post("/api/director/local")
def director_local(body: dict):
    """本地片子的「这一秒要不要说话」。

    ## 为什么单开一条

    本地片子在她硬盘上，服务端够不到 —— P2 那套预分析跑不了。
    所以帧差由**浏览器**用 canvas 实时算（`lib/localObserver.js`），
    算出来的原始分数丢过来。

    ## ⚠️ 只让浏览器算像素，判断全在这边

    第一反应是把阈值逻辑也搬进 JS —— 那样就有**两份**阈值、两份
    「什么算切镜头」的定义，改一处忘一处。

    所以浏览器只干它非干不可的活（读像素），
    `derive_events` 和 `decide` 还是这边这两个**已经测过**的函数。

    body: `{key, duration_s, position_ms, samples: [[秒, 帧差], ...], subs, user_active}`
    """
    key = str((body or {}).get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key required")

    st = _directors.setdefault(
        f"local:{key}",
        director.DirectorState(duration_s=float(body.get("duration_s") or 0)),
    )
    now_ms = int(body.get("position_ms") or 0)
    if body.get("user_active"):
        director.note_user(st, now_ms)

    # 浏览器送来的是 [[秒, 分数], ...]，秒是**视频里的绝对时间**
    raw = body.get("samples") or []
    samples = [(float(t), float(s)) for t, s in raw
               if isinstance(t, (int, float)) and isinstance(s, (int, float))]
    samples.sort()

    events = []
    if len(samples) >= 8:
        # offset 给 0：样本自带绝对时间。own 范围放开 —— 滚动窗口没有"归属"问题
        got = observer.derive_events(samples, 0, 0, 10 ** 12)
        events = got["events"]

    d = director.decide(now_ms, events, body.get("subs") or [], st)
    out = {"speak": d.speak, "reason": d.reason, "at_ms": d.at_ms,
           "spoke_count": st.spoke_count, "events": len(events)}
    if not d.speak:
        return out

    director.note_spoke(st, d, now_ms)
    # ⚠️ 画面不在这儿取 —— 片子在她机器上。前端自己 canvas 抓帧
    # 再走 /api/vision/frame，那条路本来就有
    out["evidence"] = {"dialogue": d.dialogue, "trigger": d.event}
    return out


# ⚠️ **这条必须排在 `/api/director/{sid}` 之前。**
# FastAPI 按声明顺序匹配 —— 反过来的话 "local" 会被当成一个 sid，
# 走进上面那个处理器、撞上「session 不存在」，返回一个莫名其妙的 404。
# 2026-08-23 就是这么栽的：接口写好了、部署了，一调 404。
@app.post("/api/director/{sid}")
def director_tick(sid: str, body: dict):
    """要不要在这一刻开口。前端每几秒问一次。

    返回 `{speak, reason, at_ms, evidence:{dialogue, frame}}`。
    **speak 为 false 时 reason 一定说得出为什么** —— 「他今天一句话没说」
    和「他判断了 800 次、780 次因为有台词」是两回事。

    ⚠️ **判 speak 的同时就记账**（乐观），不等前端回话。
    前端生成失败的话代价是「他这次没说、而且要等下一个冷却」——
    比「回调丢了导致同时说两句」好得多。
    """
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    st = _directors.setdefault(
        sid, director.DirectorState(duration_s=sessions[sid].get("duration") or 0)
    )
    now_ms = int(body.get("position_ms") or 0)

    # 她刚说过话/刚操作 → 让她看片
    if body.get("user_active"):
        director.note_user(st, now_ms)

    an, _ = _analyzer_for(sid)
    # 只看已经播过的那一段的事件 —— 后面的不许碰（防剧透红线）
    events = an.events(0, now_ms)
    d = director.decide(now_ms, events, _subs_of(sid), st)

    out = {"speak": d.speak, "reason": d.reason, "at_ms": d.at_ms,
           "spoke_count": st.spoke_count}
    if not d.speak:
        return out

    director.note_spoke(st, d, now_ms)
    # 便宜的那层说了「何时」，现在轮到贵的那层说「什么」
    frame_desc, frame_note = None, ""
    try:
        frame_desc, frame_note, _ = _look_at(sid, d.at_ms / 1000.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("主动点评取画面失败 %s: %s", sid, exc)
        frame_note = f"取画面失败：{type(exc).__name__}"

    out["evidence"] = {
        "dialogue": d.dialogue,
        "frame": frame_desc,
        "frame_note": frame_note,
        "trigger": d.event,
    }
    out["spoke_count"] = st.spoke_count
    return out


@app.get("/api/session/{sid}")
def session_info(sid: str):
    if sid not in sessions:
        raise HTTPException(404, "session 不存在")
    s = sessions[sid]
    return {
        "session_id": sid,
        "title": s["title"],
        "duration": s["duration"],
    }


if __name__ == "__main__":
    import uvicorn

    # 🔴 绑回环，不绑 0.0.0.0（2026-09-12 改，审计 0.6「绑定收口」）。
    #
    # 原来是 0.0.0.0 —— 这个服务**自己零鉴权**，唯一的门禁是 Caddy 那条
    # `/watch/<token>/` 路径。绑 0.0.0.0 等于「安全组一旦被改宽，门就没了」：
    # 绕过 Caddy 直连 3200 就完全不需要 token。
    #
    # 为什么现在改是安全的（改之前逐条查过）：
    #   · Caddy 反代的是 localhost:3200            → 不受影响
    #   · bridge 的健康探针用的是 127.0.0.1:3200（`WATCH_URL` 的默认值）→ 不受影响
    #   · 查的时候到 3200 没有任何活动连接
    #
    # 留 WATCH_HOST 是为了万一要临时放开（比如本机外面调试），
    # 但**默认值必须是回环** —— 安全的那一侧当默认，要放开得显式说。
    uvicorn.run(app, host=os.environ.get("WATCH_HOST", "127.0.0.1"), port=3200)
