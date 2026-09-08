/**
 * 表情 tag 四处要对得上（2026-09-08）。
 *
 * ## 为什么要有这个
 *
 * 同一份 tag 清单散在四个文件里：
 *
 *   nox-core/agent/llm.py            MEME_TAGS —— 他能选哪些（写进 prompt）
 *   bridge/server.js                 MEME_TAGS —— 白名单，不认的直接 400
 *   nox-app/frontend/src/memes.js    tag → 图片（手机端）
 *   nox-app/caelum-os-ui/src/lib/memes.js  同上（桌面端）
 *
 * commit 851a27d 的标题就叫「表情 tag **五处**同步改名」——
 * 人肉数着改，而那次仍然漏了第六处（两个测试文件），suite 红了两条没人发现。
 *
 * 失败的样子分两种，都不报错：
 *   - llm.py 有、bridge 没有 → 他发出来的表情被 400 掉，**整条消息人间蒸发**
 *   - 两边都有、memes.js 没有 → 落库成功，前端查不到图，气泡是空的
 *
 * **注释拦不住，测试才拦得住**（同 pricing.test.js 那条）。
 *
 * ⚠️ 不打网络、不起服务 —— 直接读四个源文件的文本。
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(here, "..", "..", ...p), "utf8");

/** `MEME_TAGS = (...)` / `new Set([...])` 里的字符串字面量。 */
function tagsIn(src, marker, close) {
  const block = src.split(marker)[1].split(close)[0];
  //: 单双引号都收 —— Python 那份用双引号，JS 那份也是，但别赌
  return [...block.matchAll(/["']([^"']+)["']/g)].map((m) => m[1]);
}

const core = tagsIn(read("nox-core", "agent", "llm.py"), "MEME_TAGS = (", ")");
const gate = tagsIn(read("bridge", "server.js"), "const MEME_TAGS = new Set([", "]);");

/**
 * memes.js 是 `{ tag: '/memes/x.png' }`，键**可能带引号也可能不带**。
 *
 * 🔴 带引号的那种是因为 tag 里有空格（`"where my kiss"`）——
 * 第一版这里写的是 `['"]?([^\s:'"]+)['"]?`，把带空格的键整个漏掉了，
 * 于是测试报「memes.js 缺 where my kiss」而实际上它就在第 38 行。
 * **正则漏抠的表现是「发现了一个不存在的 bug」**，比漏报更浪费时间。
 */
function memeKeys(src) {
  const block = src.split("export const memes = {")[1].split("};")[0];
  return [...block.matchAll(/^\s*(?:"([^"]+)"|'([^']+)'|([^\s:'"]+))\s*:/gm)]
    .map((m) => m[1] ?? m[2] ?? m[3]);
}
const app = memeKeys(read("nox-app", "frontend", "src", "memes.js"));
const os = memeKeys(read("nox-app", "caelum-os-ui", "src", "lib", "memes.js"));

const missing = (from, into) => from.filter((t) => !new Set(into).has(t));

test("他能选的 tag，bridge 都得放行", () => {
  //: 🔴 这个方向最要命：被 400 掉的消息 content 是空、meta.meme 查不到图，
  //: 她那边看到的是**什么都没有**，比看到一句报错糟糕得多
  assert.deepEqual(
    missing(core, gate), [],
    "llm.py 里有但 bridge 白名单没有 —— 他发这些会被 400，整条消息消失",
  );
});

test("能落库的 tag，两个前端都得有图", () => {
  assert.deepEqual(missing(core, app), [], "手机端 frontend/src/memes.js 缺图");
  assert.deepEqual(missing(core, os), [], "桌面端 caelum-os-ui/src/lib/memes.js 缺图");
});

test("两个前端的清单一致", () => {
  //: 两份手动同步的文件，早晚会分叉。分叉的表现是「手机上有图、
  //: 桌面上是空白」—— 这种差异没人会主动去比
  assert.deepEqual(missing(app, os), [], "手机端有、桌面端没有");
  assert.deepEqual(missing(os, app), [], "桌面端有、手机端没有");
});

test("清单不是空的（正则没抠错就该有几十个）", () => {
  //: 上面几条比的是差集 —— 如果正则抠出了空数组，差集也是空，**全绿**。
  //: 这条守的是「测试本身失效了但看起来很健康」那种最坏情况
  for (const [name, list] of [["llm.py", core], ["bridge", gate],
                              ["frontend", app], ["caelum-os-ui", os]]) {
    assert.ok(list.length >= 40, `${name} 只抠出 ${list.length} 个 tag，正则怕是失配了`);
  }
});
