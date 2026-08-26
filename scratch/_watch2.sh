#!/bin/bash
Q=/root/eryu/server/data/music_remote.json
echo "盯着队列文件… 现在让小克放一串（40 秒内）"
echo

for i in $(seq 1 80); do
  if [ -f "$Q" ]; then
    echo "★ 抓到写入内容："
    cat "$Q"; echo; echo
    python3 - "$Q" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
song = d.get("song") or {}
print("外层是不是 {song:...} 结构：", "是" if d.get("song") else "❌ 不是（扁平的一首）")
print("当前曲：", song.get("name"), "| artist =", repr(song.get("artist")))
q = d.get("queue")
if not q:
    print("❌ 还是没有 queue")
    if not song.get("artist"):
        print("   → 原因找到了：artist 是空的，兜底那段 `if not rest and artist` 直接跳过")
else:
    print("✅ queue %d 首：" % len(q), [t.get("name") for t in q[:4]])
PY
    exit 0
  fi
  sleep 0.5
done

echo "40 秒没抓到。看 Core 日志："
journalctl -u nox-core --since "3 min ago" --no-pager | grep -iE "eryu_play|eryu_search|点给她|排了|失败" | tail -8
