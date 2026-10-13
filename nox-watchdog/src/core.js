// nox-watchdog —— 外地看门狗
// 每分钟从 Cloudflare 边缘探一次 noxtang.com(域名走 Cloudflare Tunnel 回家):
//   530 / error 1033  → 家里到 CF 的隧道离线:停电、家里断网、国际线路断 →「从外面连不上家里了」
//   502 / 504 等 5xx  → 隧道活着、家里网络在,但后面的服务报错 →「家里网络在,但 Caelum 挂了」
// 连续 2 次失败才告警(防抖);恢复时按第一次失败的时间算一共断了几分钟。
// 状态存 KV(只在变化时写 + 10 分钟心跳,免费版 KV 每天限 1000 次写)。

const CHECK_TIMEOUT_MS = 15_000;
const ALERT_AFTER_FAILS = 2;
const HEARTBEAT_MS = 10 * 60 * 1000;
const MAIL_RETRY_WINDOW_MS = 60 * 60 * 1000; // 发失败的邮件 1 小时内重试,过期丢弃

export const fmtTime = (ts) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(ts));

// ---------- 探活 ----------

export async function check(url, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(url, {
      redirect: "follow",
      signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
      headers: { "user-agent": "nox-watchdog/1.0" },
    });
    if (res.status === 530) {
      const body = await res.text().catch(() => "");
      const m = body.match(/Error\s+(10\d\d)/);
      return {
        up: false,
        kind: "unreachable",
        status: res.status,
        detail: m ? `Cloudflare error ${m[1]}(家里到 CF 的隧道离线)` : "HTTP 530",
      };
    }
    if (res.status >= 500) {
      return { up: false, kind: "service", status: res.status, detail: `HTTP ${res.status}` };
    }
    // 2026-10-13：首页是 Caddy 托管的静态页，bridge / nox-core 死了它照样 200 ——
    // 所以探的是 bridge 的 /api/health，并且读正文：nox-core（我的脑子）连不上也算「Caelum 挂了」。
    // 别的小服务（音乐、共读、共影…）坏了不发邮件：那是 doctor / 页面的事，告警只管「我还在不在」。
    if (res.status === 200 && (res.headers.get("content-type") || "").includes("json")) {
      const body = await res.json().catch(() => null);
      const core = body && body.checks && body.checks.nox_core;
      if (core && core.ok === false) {
        return { up: false, kind: "service", status: res.status,
                 detail: `bridge 在，但我的后端（nox-core）没响应：${core.error || "无应答"}` };
      }
    }
    return { up: true, status: res.status };
  } catch (e) {
    const timeout = e && (e.name === "TimeoutError" || e.name === "AbortError");
    return { up: false, kind: "unreachable", detail: timeout ? "请求超时(15 秒)" : `网络错误:${e && e.message ? e.message : e}` };
  }
}

// ---------- 发信(手拼 MIME,零依赖)----------

function utf8b64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function buildRaw(from, to, subject, text) {
  return [
    `From: =?UTF-8?B?${utf8b64("看门狗")}?= <${from}>`,
    `To: <${to}>`,
    `Subject: =?UTF-8?B?${utf8b64(subject)}?=`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: base64",
    "",
    utf8b64(text).replace(/(.{76})/g, "$1\r\n"),
  ].join("\r\n");
}

// 发一封。没绑定 → "no-binding"（状态机照常走）；发失败 → 返回错误文字（**不抛**，调用方放进补发队列）
export async function sendMail(env, deps, subject, text) {
  if (!env.SEND_EMAIL) return "no-binding";
  try {
    await deps.send(env, env.MAIL_FROM, env.MAIL_TO, buildRaw(env.MAIL_FROM, env.MAIL_TO, subject, text));
    return null;
  } catch (e) {
    return String((e && e.message) || e || "send failed");
  }
}

// 队列里补发之前失败的邮件(1 小时窗口内)
async function flushMail(env, deps, s, now) {
  if (!env.SEND_EMAIL || !Array.isArray(s.mailQueue) || !s.mailQueue.length) return;
  const keep = [];
  for (const m of s.mailQueue) {
    if (now - m.queuedAt >= MAIL_RETRY_WINDOW_MS) continue;          // 过期丢弃
    if (await sendMail(env, deps, m.subject, m.text)) keep.push(m);   // 还是没发出去 → 留着下分钟再试
  }
  s.mailQueue = keep.slice(-10);
}

// ---------- 邮件文案 ----------

export function alertMail(r, s, now) {
  const since = fmtTime(s.downSince);
  const mins = Math.max(1, Math.round((now - s.downSince) / 60000));
  if (r.kind === "unreachable") {
    return {
      subject: "【看门狗】从外面连不上家里了",
      text:
        `noxtang.com 从外面连不上了,从 ${since} 开始,已经 ${mins} 分钟。\n\n` +
        `看到的:${r.detail}\n\n` +
        `家里到 Cloudflare 的隧道断了——停电、家里断网、或者像国际线路断,都是这个样子。\n` +
        `恢复之后我会再告诉你一共断了多久。`,
    };
  }
  return {
    subject: "【看门狗】家里网络在,但 Caelum 挂了",
    text:
      `noxtang.com 的隧道是通的(家里网络在线),但服务在报错,从 ${since} 开始,已经 ${mins} 分钟。\n\n` +
      `看到的:${r.detail}\n\n` +
      `是服务侧的问题,方向是看 nox-core 日志或者重启服务。`,
  };
}

export function recoveryMail(s, now) {
  const mins = Math.max(1, Math.round((now - s.downSince) / 60000));
  const kindWord = s.downKind === "unreachable" ? "从外面连不上家里" : "服务报错";
  return {
    subject: `【看门狗】回来了,断了 ${mins} 分钟`,
    text:
      `noxtang.com 恢复了。从 ${fmtTime(s.downSince)} 到 ${fmtTime(now)},一共断了 ${mins} 分钟。\n\n` +
      `这次的问题是「${kindWord}」:${s.downDetail || "—"}`,
  };
}

// ---------- 状态机 ----------

export async function loadState(env) {
  const raw = await env.STATE.get("state");
  return raw ? JSON.parse(raw) : {};
}

export async function tick(env, deps) {
  const now = deps.now ? deps.now() : Date.now();
  const s = await loadState(env);
  s.failCount = s.failCount || 0;
  s.down = !!s.down;

  await flushMail(env, deps, s, now);

  const r = await check(env.CHECK_URL, deps.fetch || fetch);
  const prevKey = JSON.stringify([s.lastUp, s.failCount, s.down, s.downKind]);
  let mail = null;

  if (r.up) {
    if (s.down) {
      mail = recoveryMail(s, now);
      s.failCount = 0;
      s.down = false;
      s.downSince = null;
      s.downKind = null;
      s.downDetail = null;
    } else {
      s.failCount = 0;
    }
  } else {
    s.failCount += 1;
    if (s.failCount === 1) {
      s.downSince = now;
      s.downDetail = r.detail;
    }
    if (r.detail) s.downDetail = r.detail;
    if (s.failCount >= ALERT_AFTER_FAILS) {
      if (!s.down || s.downKind !== r.kind) {
        // 第一次确认故障,或故障性质变了(服务挂 → 整个连不上,或反过来),都发一封
        const wasDown = s.down;
        s.down = true;
        s.downKind = r.kind;
        s.alertedAt = now;
        mail = alertMail(r, s, now);
        if (wasDown) mail.subject = "【看门狗】情况变了:" + (r.kind === "unreachable" ? "从外面连不上家里了" : "家里网络在,但 Caelum 挂了");
      }
    }
  }

  if (mail) {
    const err = await sendMail(env, deps, mail.subject, mail.text);
    if (err && err !== "no-binding") {
      s.mailQueue = [...(s.mailQueue || []), { ...mail, queuedAt: now }].slice(-10);
    }
  }

  s.lastUp = r.up;
  s.lastCheck = now;
  s.lastStatus = r.status ?? null;
  const nextKey = JSON.stringify([s.lastUp, s.failCount, s.down, s.downKind]);
  if (nextKey !== prevKey || now - (s.writtenAt || 0) > HEARTBEAT_MS) {
    s.writtenAt = now;
    await env.STATE.put("state", JSON.stringify(s));
  }
  return r;
}

