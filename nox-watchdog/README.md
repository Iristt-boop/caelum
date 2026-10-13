# nox-watchdog —— 外地看门狗

跑在 Cloudflare Workers 上的监控:每分钟从 CF 边缘探一次 `noxtang.com/api/health`（读正文，nox-core 连不上也算挂；10-13 前探的是首页，后端死了首页照样 200）,连续 2 次不通就发邮件到 QQ 邮箱,恢复后再发一封「回来了,断了多少分钟」。

## 两种告警怎么判定

noxtang.com 走 Cloudflare Tunnel 回家,所以从 CF 边缘看:

| 看到的 | 意思 | 邮件标题 |
|---|---|---|
| HTTP 530 / error 1033 | 家里到 CF 的隧道离线 = 停电、家里断网、国际线路断 | 【看门狗】从外面连不上家里了 |
| HTTP 502 / 504 等 5xx | 隧道活着(家里网络在),但后面的服务报错 | 【看门狗】家里网络在,但 Caelum 挂了 |
| 2xx / 3xx / 4xx | 服务活着(404 也算活) | 不发 |

- 连续 2 次失败才告警(防抖,单次抖动静默忽略)
- 断的时长从**第一次失败**那分钟算起
- 故障期间性质变了(服务挂 → 整个连不上,或反过来)会补发一封
- 发失败的邮件 1 小时内自动补发,过期丢弃

## 运维入口

- **状态页**:https://status.noxtang.com/ (纯边缘运行,不依赖家里网络;workers.dev 国内难直达所以挂了自定义域)
- **手动触发一次检查**:`https://status.noxtang.com/check?token=<WATCHDOG_TOKEN>`
- **发测试邮件**:`https://status.noxtang.com/test-mail?token=<WATCHDOG_TOKEN>`
- token 本机存在 `%TEMP%\watchdog_token.txt`,也可随时 `npx wrangler secret put WATCHDOG_TOKEN` 换新
- 状态存 KV(只在变化时写 + 10 分钟心跳,免费版每天 1000 次写限额内)

## 发信链路状态(2026-10-12 全通)

1. ✅ destination `1437291363@qq.com` 已添加并验证(她点了 QQ 邮箱里的链接)
2. ✅ `send_email` binding 已部署(wrangler 4 的 TOML 要用 `[[send_email]]` 表数组,顶层数组写法会被当普通变量)
3. ✅ `/test-mail` 实测发出「【看门狗】测试邮件」(routing enabled:false 也未挡发信)
4. ⬜ 可选加固:CF dashboard → noxtang.com → Email → Email Routing → 开用并替换 MX。官方文档要求 routing 启用才能发信,实测未开也能发;开了更稳,顺带以后想在 `@noxtang.com` 收邮件也是现成的。MX 替换安全——那套 `eforward*.registrar-servers.com` 是 Namecheap 注册商默认铺的占位转发,从没在用。

发件人是 `watchdog@noxtang.com`。QQ 邮箱如果把它扔进垃圾箱,加个白名单。

## 常用操作

```bash
cd D:\claude-code\nox-watchdog
npx wrangler deploy          # 改代码后部署(需本机 wrangler 已登录)
npx wrangler tail            # 看实时日志
```

注意:Email Routing 每天每 zone 发信限额约 100 封(告警场景远远用不到);cron 用的是免费计划(Workers Free 含 cron triggers)。

## 已知边界

- Worker 探测的是「CF 边缘 → 家里」这条链路。今天早上那种国际线路抖,如果家里 cloudflared 的连接也断了,会正确触发「从外面连不上家里」;但如果只是国内到 CF 某些节点抖、隧道还活着,看门狗看到的是正常——那种属于「你上不去但家没断」。
- 521/522(直连回源才会出现的错误码)被归到「服务挂」侧,邮件里会带原始状态码。

## 代码结构与测试（10-13）

- `src/core.js`：判定 + 状态机 + 邮件文案（纯 JS，不碰 Cloudflare）
- `src/worker.js`：入口，只把 cron / HTTP / 发信 binding 接到 core 上
- `npm test`（= `node --test test/core.test.mjs`）：7 条，含「首页 200 不算数」「发信失败不丢状态、补发失败不丢邮件」；改坏 7 处全红
- 部署：`npm run deploy`（本机 wrangler 已登录）
