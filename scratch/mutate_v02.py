"""embodied v0.2 变异：每个变异体改一处 → 跑 test_embodied → 必须红。跑完还原。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "nox-core"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

MUTANTS2 = [
    ("室内阈值退回 26", "embodied/device_model.py",
     '_env_float("NOX_BLANKET_INDOOR_MAX_C", 22)', '_env_float("NOX_BLANKET_INDOOR_MAX_C", 26)'),
    ("ha-mcp 漏掉电竞房温度", "../ha-mcp/main.py",
     '"电竞房 温度": "sensor.miaomiaoce_t2_467c_temperature",', ''),
    ("路由不认湿度", "router/intent.py",
     'r"|湿度|干不干|好干|太干|潮不潮|冷不冷|热不热"', 'r""'),
]
MUTANTS_V02 = [
    ("闸门不用室内实测", "embodied/validator.py",
     'if indoor is not None and deny_when.get("indoor_temp_above") is not None:',
     'if False:'),
    ("室内读数不判新旧", "embodied/validator.py",
     "if (datetime.now(timezone.utc) - at).total_seconds() > INDOOR_STALE_MAX_S:",
     "if False:"),
    ("哨兵不节流", "attention/sources/indoor_climate.py",
     "if self._last is not None and now - self._last < EVERY:",
     "if False:"),
    ("哨兵把 unavailable 当成 0 度写", "attention/sources/indoor_climate.py",
     "        return None\n\n\nclass",
     "        return 0.0\n\n\nclass"),
]

env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
bad = 0
MUTANTS = MUTANTS2 if "--v2" in sys.argv else MUTANTS_V02
for name, rel, old, new in MUTANTS:
    p = ROOT / rel
    src = p.read_bytes().decode("utf-8")
    hits = src.count(old)
    if hits != 1:
        print(f"!! {name}: 命中 {hits} 处，变异没生效")
        bad += 1
        continue
    p.write_bytes(src.replace(old, new).encode("utf-8"))
    try:
        r = subprocess.run([PY, "-m", "pytest", "tests/test_embodied.py", "tests/test_context_routing.py", "-q", "-p", "no:cacheprovider"],
                           cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
        print(f"{'红 ✓' if r.returncode != 0 else '没红 ✗'}  {name}: {tail}")
        bad += r.returncode == 0
    finally:
        p.write_bytes(src.encode("utf-8"))
sys.exit(1 if bad else 0)
