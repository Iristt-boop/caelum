#!/bin/bash
echo "=== index.html 引的 bundle ==="
curl -s https://noxtang.com/ > /tmp/l.html
grep -o 'assets/index-[A-Za-z0-9_-]*\.js' /tmp/l.html

echo ""
echo "=== 状态栏 meta（顶部那条）==="
grep -o 'status-bar-style. content=..[a-z-]*' /tmp/l.html

echo ""
echo "=== index.html 的缓存头 ==="
curl -sI https://noxtang.com/ | grep -i 'cache-control\|etag\|last-modified'

echo ""
echo "=== 线上 Chat chunk 里有没有那个渐变 ==="
CHUNK=$(grep -o 'assets/Chat-[A-Za-z0-9_-]*\.js' /tmp/l.html | head -1)
if [ -z "$CHUNK" ]; then
  # Chat 是懒加载的，chunk 名写在 index chunk 里
  IDX=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' /tmp/l.html | head -1)
  CHUNK=$(curl -s "https://noxtang.com/$IDX" | grep -o 'Chat-[A-Za-z0-9_-]*\.js' | head -1)
  CHUNK="assets/$CHUNK"
fi
echo "Chat chunk: $CHUNK"
curl -s "https://noxtang.com/$CHUNK" > /tmp/chat.js
echo "大小: $(wc -c < /tmp/chat.js)"
echo -n "含 'linear-gradient(to bottom, transparent 0, var(--color-bg) 26px': "
grep -c 'color-bg) 26px' /tmp/chat.js || echo 0
echo -n "含 normalizeMarkdown 的规范化逻辑: "
grep -c 'minHeight' /tmp/chat.js || echo 0
rm -f /tmp/l.html /tmp/chat.js
