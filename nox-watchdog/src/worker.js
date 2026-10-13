// nox-watchdog 入口：只管把 Cloudflare 的东西（cron、HTTP、发信 binding）接到 core.js 上。
// 判定和状态机全在 core.js（纯 JS，node --test 能直接测）。

import { EmailMessage } from "cloudflare:email";
import { fmtTime, loadState, sendMail, tick } from "./core.js";

const DEPS = {
  send: (env, from, to, raw) => env.SEND_EMAIL.send(new EmailMessage(from, to, raw)),
};

// ---------- 入口 ----------

export default {
  async scheduled(event, env) {
    await tick(env, DEPS);
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    const authed = env.WATCHDOG_TOKEN && url.searchParams.get("token") === env.WATCHDOG_TOKEN;

    if (url.pathname === "/") {
      const s = await loadState(env);
      return Response.json({
        watchdog: "nox-watchdog",
        check: env.CHECK_URL,
        lastCheck: s.lastCheck ? fmtTime(s.lastCheck) + " (北京时间)" : null,
        lastUp: s.lastUp ?? null,
        lastStatus: s.lastStatus ?? null,
        failCount: s.failCount || 0,
        down: !!s.down,
        downKind: s.downKind || null,
        downDetail: s.downDetail || null,
        mailBound: !!env.SEND_EMAIL,
        pendingMail: (s.mailQueue || []).length,
      });
    }

    if (!authed) return new Response("unauthorized", { status: 401 });

    if (url.pathname === "/check") {
      const r = await tick(env, DEPS);
      const s = await loadState(env);
      return Response.json({ ran: true, result: r, state: s });
    }

    if (url.pathname === "/test-mail") {
      const err = await sendMail(
        env,
        DEPS,
        "【看门狗】测试邮件",
        `这是 nox-watchdog 的测试邮件,收到它说明「Worker → Email Routing → QQ 邮箱」整条发信链路是通的。\n时间:${fmtTime(Date.now())}(北京时间)`
      );
      return Response.json({ sent: !err, err: err || null });
    }

    return new Response("not found", { status: 404 });
  },
};
