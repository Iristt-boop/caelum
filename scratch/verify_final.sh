#!/bin/bash
# 全链路自检。测试数据一律 test- 前缀并自清理。
T=$(systemctl show bridge -p Environment --value | tr ' ' '\n' | sed -n 's/^NOX_TOKEN=//p')
H="X-Nox-Token: $T"
J="Content-Type: application/json"
B=http://127.0.0.1:3003

echo "=== 1. 服务都活着 ==="
for s in bridge nox-core; do echo -n "$s: "; systemctl is-active $s; done

echo
echo "=== 2. 糖糖的三个例子，逐个建 ==="
mk() {
  curl -s -m 10 -H "$H" -H "$J" -d "$2" $B/api/today | sed -n 's/.*"id":"\([^"]*\)".*/\1/p'
}
ID1=$(mk 1 '{"text":"test-背单词","repeat":"daily","at":"18:00"}')
ID2=$(mk 2 '{"text":"test-加attention source","repeat":"once","due":"2026-08-18","at":"09:00"}')
ID3=$(mk 3 '{"text":"test-健身","repeat":"weekly_count","times":3,"at":"20:00"}')
echo "建好三条：$ID1 / $ID2 / $ID3"

echo
echo "=== 3. 清单里长什么样 ==="
curl -s -m 10 -H "$H" $B/api/todo/list \
  | python3 -c "import sys,json;d=json.load(sys.stdin);[print(' ',i['bucket'],'|',i['when'],'|',i['text']) for i in d['items'] if i['text'].startswith('test-')]"

echo
echo "=== 4. 到点该追哪些（现在 $(TZ=Asia/Shanghai date +%H:%M)）==="
curl -s -m 10 -H "$H" $B/api/todo/due \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('  当前',d['now'],'该追:',[i['text'] for i in d['items'] if i['text'].startswith('test-')])"

echo
echo "=== 5. 划掉循环的那条：应该只记一笔，不永久消失 ==="
curl -s -m 10 -H "$H" -H "$J" -d '{"keyword":"test-背单词"}' $B/api/todo/complete
echo
echo -n "  划完它还在清单里吗（循环任务应该还在）: "
curl -s -m 10 -H "$H" $B/api/todo/list \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('在' if any(i['text']=='test-背单词' for i in d['items']) else '不在了 —— 这就错了')"

echo
echo "=== 6. Care 层现状 ==="
curl -s -m 10 http://127.0.0.1:8100/health \
  | python3 -c "import sys,json;a=json.load(sys.stdin).get('attention') or {};c=a.get('care') or {};print('  dry_run:',a.get('dry_run'));print('  活着的关心链:',len(c.get('threads_alive') or []));print('  押后的念头:',len(c.get('held') or []))"

echo
echo "=== 7. 清理测试数据 ==="
for i in $ID1 $ID2 $ID3; do curl -s -m 10 -X DELETE -H "$H" $B/api/today/$i > /dev/null; done
curl -s -m 10 -H "$H" $B/api/todo/list \
  | python3 -c "import sys,json;d=json.load(sys.stdin);n=[i for i in d['items'] if i['text'].startswith('test-')];print('  残留测试数据:',len(n))"
