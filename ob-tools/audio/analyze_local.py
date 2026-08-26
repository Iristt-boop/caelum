#!/usr/bin/env python3
"""本地音频分析 —— 在糖糖的电脑上跑，结果回传 VPS。

## 为什么在本地跑

VPS 上没装 librosa，而且那台机器同时跑着 bridge / nox-core / ombre-brain /
eryu / netease-mcp / ha-mcp / xiaozhi，突然多一个吃满 CPU 的音频分析
会拖累对话响应。糖糖的 i5-13400F 有 16 个逻辑核，闲着也是闲着。

**eryu 本来就允许这么做** —— 它的 `analyze_song.py` 是独立子进程，
结果写成 `{song_id}_preanalysis.json`，服务端 `/music/analyze/status`
只是去读那个文件。分析在哪台机器上跑，它不关心。

## 和 eryu 原版的两处区别

1. **修了 BPM 那行**。原版 `round(float(tempo))` 在 librosa 0.10+ 会炸：
   `beat_track` 现在返回数组不是标量，numpy 2.x 不许直接 float() 一个
   1 维数组 —— 报 `only 0-dimensional arrays can be converted to Python
   scalars`（2026-08-09 实测）。
2. **不画频谱图**。原版会 import matplotlib 存一张 PNG，我们用不上，
   白占时间和磁盘。

## 多出来的东西

原版只给 bpm / key / segments。这里多算了几个**能直接用来选歌**的：

    energy      整首歌的平均能量（0~1）—— 「治愈」和「打扫」的分水岭
    dynamics    能量起伏大小 —— 平缓的适合睡前，起伏大的适合提神
    brightness  频谱质心，高频占比 —— 亮/暗，比 BPM 更能区分气质
    vocal_ratio 谐波占比的粗估 —— 低的大概率是纯音乐

BPM 只能区分快慢，区分不了「治愈」和「悲伤」（那俩都可能是慢歌）。
上面这几个才是场景选歌真正要用的。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def analyze(audio_path: Path, song_id: str, name: str = "", artist: str = "") -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio_path), sr=22050)
    duration = float(librosa.get_duration(y=y, sr=sr))

    # ⚠️ librosa 0.10+ 的 beat_track 返回 array，不是标量。
    # 原版直接 float(tempo) 会被 numpy 2.x 拒掉
    tempo_raw, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo_raw)[0])

    rms = librosa.feature.rms(y=y)[0]
    times_rms = librosa.times_like(rms, sr=sr)

    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    dominant_key = keys[int(np.argmax(np.mean(chroma, axis=1)))]

    # 分段能量曲线（同原版，6 段）
    seg_count = 6
    seg_len = max(1, len(rms) // seg_count)
    segments = []
    for i in range(seg_count):
        seg = rms[i * seg_len:(i + 1) * seg_len]
        if not len(seg):
            continue
        segments.append({
            "start": round(float(times_rms[i * seg_len]), 1),
            "end": round(float(times_rms[min((i + 1) * seg_len - 1, len(times_rms) - 1)]), 1),
            "avgEnergy": round(float(np.mean(seg)), 4),
            "maxEnergy": round(float(np.max(seg)), 4),
        })

    # ---- 选歌真正要用的特征 ----
    energy = float(np.mean(rms))
    dynamics = float(np.std(rms))
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    brightness = float(np.mean(centroid))

    # 谐波/打击分离。谐波占比高 ≈ 旋律为主（人声、弦乐），
    # 低 ≈ 节奏为主。用来粗估「像不像纯音乐」，不精确但够分档
    y_h, y_p = librosa.effects.hpss(y)
    h_energy = float(np.mean(np.abs(y_h)))
    p_energy = float(np.mean(np.abs(y_p)))
    harmonic_ratio = h_energy / (h_energy + p_energy + 1e-9)

    return {
        "songId": song_id,
        "name": name,
        "artist": artist,
        "duration": round(duration, 1),
        "bpm": round(tempo),
        "key": dominant_key,
        "segments": segments,
        # 下面这些是我们加的
        "energy": round(energy, 4),
        "dynamics": round(dynamics, 4),
        "brightness": round(brightness, 1),
        "harmonicRatio": round(harmonic_ratio, 3),
        "analyzedBy": "local",
        "analyzedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="本地音频分析")
    ap.add_argument("audio", help="mp3 路径")
    ap.add_argument("--id", help="song_id，默认取文件名")
    ap.add_argument("--name", default="")
    ap.add_argument("--artist", default="")
    ap.add_argument("--out", help="输出目录，默认和 mp3 同目录")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print("找不到音频：%s" % audio)
        return 1

    song_id = args.id or audio.stem
    out_dir = Path(args.out) if args.out else audio.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        result = analyze(audio, song_id, args.name, args.artist)
    except Exception as exc:
        print("❌ 分析失败：%s: %s" % (type(exc).__name__, exc))
        return 1
    dt = time.time() - t0

    out = out_dir / ("%s_preanalysis.json" % song_id)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print("✅ %.1f 秒 · %s" % (dt, out.name))
    print("   时长 %.0f 秒 | BPM %s | 调性 %s" % (
        result["duration"], result["bpm"], result["key"]))
    print("   能量 %.3f | 起伏 %.3f | 明亮度 %.0fHz | 谐波占比 %.2f" % (
        result["energy"], result["dynamics"], result["brightness"],
        result["harmonicRatio"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
