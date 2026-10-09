/**
 * 聊天模型选择的代理（2026-10-09）。
 *
 * 守两件事：路由真的挂着；Core 回的 400 / 503 原样传给前端（翻成 200 她会以为切好了）。
 * 源码文本断言 —— 起 bridge 要连 DB 和一堆下游，为两条透传不值得。
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "server.js"), "utf8");
const block = src.split('app.get("/api/nox/model", async')[1]?.split("/* 关系状态")[0] ?? "";

test("GET / POST /api/nox/model 都挂着，且转给 Core 同一个路径", () => {
  assert.ok(block.length > 0, "找不到 /api/nox/model 这段");
  assert.match(src, /app\.post\("\/api\/nox\/model"/);
  assert.equal((block.match(/\/api\/nox\/model`/g) || []).length, 2);
});

test("Core 的状态码原样传回（400 / 503 不许被翻成 200）", () => {
  assert.equal((block.match(/res\.status\(r\.status\)\.json\(await r\.json\(\)\)/g) || []).length, 2);
});

test("POST 只带 key 这一个字段给 Core", () => {
  assert.match(block, /JSON\.stringify\(\{ key: String\(req\.body\?\.key \?\? ""\) \}\)/);
});
