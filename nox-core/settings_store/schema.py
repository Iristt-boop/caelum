"""配置层的表结构（设计：Caelum-配置层-设计稿-2026-10-05.md 第五节）。

🔴 **这个库里不存任何 key / token / 密码。** providers 只存「去哪个环境变量里取 key」的**名字**
（`key_envs`）。key 本身（P2 起）单独放一个 0600 文件，不和这张库在一起 ——
库会被备份、被拷、被截图，不该因此带出密钥。
`tests/test_settings_store.py` 有一条断言：任何表都不许出现叫 key / token / secret / password 的列。

P0 只有 `source='seed'` 的行（从代码和环境变量灌的）；`source='user'` 是 P2 起前端改出来的行，
**种子刷新永远不碰它们**。
"""

from __future__ import annotations

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

-- 一条连接。cache_style 取代 agent/adapters.py 里「URL 里有没有 openrouter」的嗅探。
CREATE TABLE IF NOT EXISTS providers(
  id          TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  protocol    TEXT NOT NULL,                 -- openai_compat | anthropic
  base_url    TEXT NOT NULL,
  cache_style TEXT NOT NULL,                 -- explicit_breakpoint | passthrough | auto_prefix | none
  auth        TEXT NOT NULL DEFAULT 'api_key',
  key_envs    TEXT NOT NULL DEFAULT '[]',    -- JSON：取 key 的环境变量**名字**，按顺序找第一个非空的
  source      TEXT NOT NULL,                 -- seed | user
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 挂在厂商下的型号。short_name 是前端下拉 / 请求里带的短名（config.models 的 key）。
CREATE TABLE IF NOT EXISTS models(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id  TEXT NOT NULL REFERENCES providers(id),
  model_name   TEXT NOT NULL,
  short_name   TEXT UNIQUE,                  -- 可空：env 里直接指定的、不在下拉清单里的型号
  label        TEXT NOT NULL DEFAULT '',
  capabilities TEXT NOT NULL DEFAULT '["chat"]',   -- JSON 列表：chat / vision / tts / stt / image / embedding
  price_json   TEXT,                         -- 手填的价格（元/百万 tokens），可空；不自动抓取、不自动维护
  origin       TEXT NOT NULL,                -- seed | fetched | manual
  enabled      INTEGER NOT NULL DEFAULT 1,
  fetched_at   TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE(provider_id, model_name)
);

-- 功能槽位：谁用哪个型号。targets 是有序列表（单值槽位只有一项；TTS 将来是降级链）。
CREATE TABLE IF NOT EXISTS slots(
  slot        TEXT PRIMARY KEY,              -- chat.primary | chat.utility | vision | …
  targets     TEXT NOT NULL,                 -- JSON：[{"model_id": n, "params": {...}}]
  updated_at  TEXT NOT NULL,
  updated_by  TEXT NOT NULL                  -- seed | she | him-proposal
);

-- 每次改槽位存一份快照，给「回到上一版」用（P3）。
CREATE TABLE IF NOT EXISTS slot_history(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  slot          TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  at            TEXT NOT NULL,
  reason        TEXT NOT NULL DEFAULT ''
);

-- 最近一次真测的结论（不存回答正文）（P2）。
CREATE TABLE IF NOT EXISTS test_results(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  target      TEXT NOT NULL,
  kind        TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  latency_ms  INTEGER,
  detail      TEXT NOT NULL DEFAULT '',
  at          TEXT NOT NULL
);

-- P0 的验收账本：每次启动的影子对比结果。一周后看「有没有哪次不一致」就查这张表。
CREATE TABLE IF NOT EXISTS shadow_log(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  n_checked  INTEGER NOT NULL,
  n_diff     INTEGER NOT NULL,
  seed_added INTEGER NOT NULL DEFAULT 0,
  detail     TEXT NOT NULL DEFAULT '[]'      -- JSON：不一致的路径 + 摘要（密钥只留摘要前缀，不留值）
);
"""

#: 任何表里都不许出现的列名片段（测试用）。
FORBIDDEN_COLUMN_HINTS = ("key", "token", "secret", "password", "passwd")
#: 例外：`key_envs` 存的是环境变量**名字**，不是 key；`k` 是 meta 的键名。
ALLOWED_COLUMNS = {"key_envs"}
