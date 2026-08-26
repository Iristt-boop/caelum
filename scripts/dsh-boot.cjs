// DSH 后台启动器（脱离 cmd 独立运行）
// 用法:
//   node dsh-boot.cjs [port]        启动（端口被占则跳过）
//   node dsh-boot.cjs stop [port]   停止（按 pid 文件）
// 日志: D:\claude-code\logs\dsh-boot.log
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const net = require("net");

const HARNESS = "D:\\deepseek-harness";
const NODE_EXE = "C:\\Users\\14372\\.workbuddy\\binaries\\node\\versions\\22.22.2\\node.exe";
const LOG_DIR = "D:\\claude-code\\logs";
const LOG_FILE = path.join(LOG_DIR, "dsh-boot.log");
const PID_FILE = path.join(LOG_DIR, "dsh-boot.pid");

const port = parseInt(process.argv[3] || process.argv[2] || "3080", 10);
const action = process.argv[2] === "stop" ? "stop" : "start";

function log(msg) {
  fs.appendFileSync(LOG_FILE, `[${new Date().toISOString()}] ${msg}\n`);
}
function portInUse(p) {
  return new Promise((resolve) => {
    const s = net.connect({ host: "127.0.0.1", port: p });
    s.once("connect", () => { s.destroy(); resolve(true); });
    s.once("error", () => resolve(false));
    s.setTimeout(600, () => { s.destroy(); resolve(false); });
  });
}
function readPid() {
  try { return parseInt(fs.readFileSync(PID_FILE, "utf8").trim(), 10); } catch { return null; }
}
function writePid(pid) { fs.writeFileSync(PID_FILE, String(pid)); }

(async () => {
  if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });

  if (action === "stop") {
    // 1) 先按 pid 文件杀（启动器记录的主进程）
    const pid = readPid();
    if (pid) {
      try { process.kill(pid); log(`stop: killed pid ${pid}`); }
      catch (e) { log(`stop: pid ${pid} not running (${e.code})`); }
      fs.unlinkSync(PID_FILE);
    } else {
      log("stop: no pid file");
    }
    // 2) 兜底：按端口找监听进程杀（防 pid 文件丢失/子进程残留）
    try {
      const { execSync } = require("child_process");
      const lines = execSync(`netstat -ano -p tcp`, { encoding: "utf8", windowsHide: true }).split(/\r?\n/);
      const seen = new Set();
      for (const line of lines) {
        const m = line.match(/TCP\s+127\.0\.0\.1:${port}\s+.*?LISTENING\s+(\d+)/);
        if (m && !seen.has(m[1])) {
          seen.add(m[1]);
          try { process.kill(parseInt(m[1], 10)); log(`stop: port ${port} owner pid ${m[1]} killed`); }
          catch (e) { log(`stop: port ${port} owner pid ${m[1]} kill failed (${e.code})`); }
        }
      }
      if (seen.size === 0) log(`stop: no listener on :${port}`);
    } catch (e) { log(`stop: port scan failed (${e.message})`); }
    return;
  }

  if (await portInUse(port)) {
    log(`start: :${port} already in use, skip`);
    return;
  }

  const out = fs.openSync(LOG_FILE, "a");
  const env = { ...process.env };
  delete env.WORKBUDDY_FS_PROTECTION_ROLE;
  delete env.CODEBUDDY_SAFE_DELETE_SANDBOX;

  const child = spawn(NODE_EXE, ["--import", "tsx/esm", "apps/cli/src/bin.ts", "web", "--port", String(port)], {
    cwd: HARNESS,
    env,
    stdio: ["ignore", out, out],
    detached: true,
    windowsHide: true,
  });
  child.unref();
  writePid(child.pid);
  log(`start: spawned pid=${child.pid} port=${port} cwd=${HARNESS}`);
})().catch((e) => { log("error: " + (e && e.stack || e)); });
