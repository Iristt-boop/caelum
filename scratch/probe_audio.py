"""B站拉流没声音 —— 看看它到底给了哪些格式。只读。

怀疑：B站和 YouTube 一样是 DASH，音视频**分轨**。
`_fresh_direct_url` 里那句「优先返回纯视频轨」等于主动把声音扔了。
"""
import sys

sys.path.insert(0, "/root/co-watching")
import app  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1G48M6XEBt"
info = app._extract(URL)
print("=== ", info.get("title"))

req = info.get("requested_formats") or []
print(f"\n=== requested_formats（yt-dlp 按 FORMAT 选出来的）：{len(req)} 段")
for f in req:
    print(f"    {f.get('format_id'):>12} v={f.get('vcodec')!s:<12} a={f.get('acodec')!s:<12} "
          f"{f.get('height')}p {f.get('ext')}")

allf = info.get("formats") or []
muxed = [f for f in allf
         if (f.get("vcodec") or "none") != "none" and (f.get("acodec") or "none") != "none"]
print(f"\n=== 所有格式 {len(allf)} 个，其中**音视频都有**的 {len(muxed)} 个")
for f in muxed:
    print(f"    {f.get('format_id'):>12} {f.get('height')}p {f.get('ext'):<5} "
          f"v={f.get('vcodec')} a={f.get('acodec')} "
          f"{(f.get('filesize') or f.get('filesize_approx') or 0)//1048576} MB")

audio = [f for f in allf if (f.get("vcodec") or "none") == "none"]
print(f"\n=== 纯音频轨 {len(audio)} 个")
for f in audio[:4]:
    print(f"    {f.get('format_id'):>12} a={f.get('acodec')} {f.get('abr')} kbps {f.get('ext')}")

print("\n=== 现在 _fresh_direct_url 会选谁")
app.sessions["p"] = {"id": "p", "info": info, "title": "", "duration": 0,
                     "created": 0, "extracted_at": 0}
u = app._fresh_direct_url("p")
hit = [f for f in (req or allf) if f.get("url") == u]
if hit:
    f = hit[0]
    print(f"    {f.get('format_id')} v={f.get('vcodec')} a={f.get('acodec')}"
          f"  ← acodec 是 none 就说明没声音")
