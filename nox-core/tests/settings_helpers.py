"""配置层测试共用的小工具：造一份确定的 `Config` + 已知标记串的密钥。"""

from __future__ import annotations

from pathlib import Path

from config import Config

#: 已知标记串。测试用它们扫日志 / 返回值 / 库文件：任何地方出现 = 密钥泄漏。
MARKERS = {
    "DEEPSEEK_API_KEY": "KEY-MARK-DEEPSEEK-111",
    "OPENROUTER_API_KEY": "KEY-MARK-OPENROUTER-222",
    "ZHIPU_API_KEY": "KEY-MARK-ZHIPU-333",
    "ANTHROPIC_API_KEY": "KEY-MARK-ANTHROPIC-444",
    "DASHSCOPE_API_KEY": "KEY-MARK-DASHSCOPE-555",
}

_SLOT_ENVS = [
    f"NOX_{s}_{f}"
    for s in ("PRIMARY", "UTILITY", "VISION")
    for f in ("BACKEND", "MODEL", "PROVIDER", "BASE_URL", "MAX_TOKENS", "API_KEY")
]


def make_cfg(monkeypatch, **env: str) -> Config:
    """清掉所有会影响三个槽位的环境变量，设成确定的值，再造一份真的 `Config`。

    默认：主线 DeepSeek（deepseek-flash）、杂活智谱（glm-5.3-flash）、识图沿用 Config 的默认；
    每家都配了带标记串的 key。`env` 里的键值覆盖默认（空串 = 清掉）。
    """
    for name in _SLOT_ENVS:
        monkeypatch.setenv(name, "")
    base = {
        "NOX_PRIMARY_BACKEND": "deepseek", "NOX_PRIMARY_MODEL": "deepseek-flash",
        "NOX_UTILITY_BACKEND": "zhipu", "NOX_UTILITY_MODEL": "glm-5.3-flash",
        **MARKERS,
    }
    base.update(env)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    return Config()


def scan_bytes(path: Path) -> bytes:
    """库文件 + WAL + SHM 的全部字节（SQLite 的 WAL 里可能还留着没合并的页）。"""
    return b"".join(p.read_bytes() for p in sorted(path.parent.glob(path.name + "*")))


def assert_no_markers(blob, where: str) -> None:
    text = blob if isinstance(blob, str) else blob.decode("utf-8", "ignore")
    for name, mark in MARKERS.items():
        assert mark not in text, f"{where} 里出现了 {name} 的标记串 —— 密钥泄漏"
    assert "KEY-MARK-UTILITY-OVERRIDE" not in text, f"{where} 里出现了 utility 覆盖 key 的标记串"
