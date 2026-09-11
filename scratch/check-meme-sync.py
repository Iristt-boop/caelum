# 临时：验 bridge/test/meme-tags.test.js 的四个正则能不能抠对
# （本机没装 node，那个测试只能在 VPS 跑，所以先在这儿验正则本身）
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
rd = lambda p: open(p, encoding="utf-8").read()

STR = re.compile(r"""["']([^"']+)["']""")
# 带引号的键是因为 tag 里有空格（"where my kiss"）—— 不认这种的话
# 会报「memes.js 缺 where my kiss」而它其实就在那儿
KEY = re.compile(r"""^\s*(?:"([^"]+)"|'([^']+)'|([^\s:'"]+))\s*:""", re.M)


def tags_in(src, marker, close):
    return [m.group(1) for m in STR.finditer(src.split(marker)[1].split(close)[0])]


def meme_keys(src):
    return [next(g for g in m.groups() if g) for m in KEY.finditer(
        src.split("export const memes = {")[1].split("};")[0])]


core = tags_in(rd("nox-core/agent/llm.py"), "MEME_TAGS = (", ")")
gate = tags_in(rd("bridge/server.js"), "const MEME_TAGS = new Set([", "]);")
app = meme_keys(rd("nox-app/frontend/src/memes.js"))
osui = meme_keys(rd("nox-app/caelum-os-ui/src/lib/memes.js"))

for n, l in (("llm.py", core), ("bridge", gate), ("frontend", app), ("os-ui", osui)):
    print(f"{n:10} {len(l):3} 个   前3: {l[:3]}")

miss = lambda a, b: [t for t in a if t not in set(b)]
print()
print("llm→bridge 缺:  ", miss(core, gate))
print("llm→frontend 缺:", miss(core, app))
print("llm→os-ui 缺:   ", miss(core, osui))
print("frontend↔os-ui: ", miss(app, osui), miss(osui, app))
