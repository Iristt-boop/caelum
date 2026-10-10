# Nox 搬家调研：东京 VPS → N150 小主机

> 2026-09-21 定稿 · 决策文档
> 背景：东京机（43.153.154.237，2核/3.6G）2026-11-01 到期，不再续费。
> 动机：省 VPS 费用 + 设备线并线 + 记忆/核心数据回到自己手里。
> 本文档自包含：10 月下旬动工时按此执行，不需要回看聊天记录。
>
> **2026-09-21 修订（小克对着代码核完后补）**：初版把「VPS 的活」归成中继/代理/备份
> 三项，漏了第四项——**公网 HTTPS 服务端点**，而 Tailscale 顶不了这一项。
> 修订涉及四处，正文中标了 `🔧 修订` ：
> ① 决策 2 改为 **CF Tunnel + Tailscale 双轨**（§四）
> ② 新增 **告警不能跟着搬**（§六·五）
> ③ 新增 **数据搬迁清单 + 备份缺失台账**（§五·五）
> ④ 装机方式从 rsync 改为 **从备份包起**，顺带做掉排期 `5.2`（§七）
>
> **2026-10-10 变更（她拍板：提前动工）**：动工时间从「10 月下旬」提前到 **10-10/11 这两天**。
> 直接动因：家宽链路对 noxtang.com 的 DNS 抢答劫持持续（两个冒充 IP：141.193.154.210 /
> 103.73.220.77，同款 redirect-cnzz 假证书，时有时无），手机装了阿里 DoH 也会因 iOS 在
> DoH 通道不稳时回退明文而漏防——WiFi 下快捷指令的健康/睡眠/App 上报已实际断供
> （蜂窝与 VPN 干净）。Tailscale/CF Tunnel 入口是唯一治本。
> 安全审计（10-10 晚，服务器三天日志）：**秘钥零泄漏、系统零接触**——假证书让 TLS 在
> 发出任何数据前就失败，攻击者 IP 段对真服务器请求数为 0，token 无第二来源，0 次 401。
> 影响只有数据缺口（撞上窗口的上报丢失）。过渡期：手机 VPN 常开。
> 对施工的含义：**Tailscale 轨道从 shadow 观察项升为第一天必须项**，时间线压缩见 §七 表前说明。

---

## 一、结论摘要（TL;DR）

1. **可行且不难搬**：nox 这套的重活全在云端 API，本地只是轻量 Node/Python 服务，N150 绰绰有余。工期一个晚上 + 一周 shadow 观察。
2. **终态按 ¥0 档动手**：N150 跑全套 + **CF Tunnel（公网门）+ Tailscale（内网快线）双轨** + 全链路国内化 + GitHub 私仓异地备份。东京机在 shadow 期间原地不动当保底，切换完成后退租。
   🔧 *修订：初版只写 Tailscale。Tailscale 接不住 claude.ai 云端发起的 MCP connector，详见 §四·决策 2。*
3. **TTS 换国内套餐**（已拍板）：火山豆包或 MiniMax，用音色复刻保留 nox 现在的声音；edge TTS 留作免费兜底（国内 CDN 本来就快）。
4. **翻墙保留**：Reality 迁到一台年付 $21.99 的 RackNerd 美西小鸡（1核1G 足够），10 月中下旬买、复制配置即走。
5. **搬家白送的**：HA 设备线并线（清掉生态调研第一债）、操作她 PC 的延迟从 ~120ms 降到 1-2ms、WOL 叫醒电脑、Proxmox 级备份回滚。
6. 🔧 **顺带做掉排期 `5.2`（备份从没真正恢复过）**：9-19 暂缓的理由是「没有临时干净机器」，现在有了。前提是装机**从备份包起、不 rsync**——见 §七。
7. 🔧 **退租前必须先补外部告警**：`caelum-watch` 现在住在东京机上，搬到 N150 = 自己监控自己，停电断网时它跟着一起死。见 §六·五。

---

## 二、实测数据（2026-09-21，糖糖家网络）

> 她 PC 和 N150 同一家庭网络，PC 上的数字 ≈ N150 未来的数字。
> VPS 数据同日从东京机实测。TCP 耗时 ≈ 连接复用后每次请求的真实往返成本。

| 链路 | 家里（N150 未来） | 东京 VPS（现状） |
|---|---|---|
| → ElevenLabs (ele-v3) | total 1.28s / **TCP 290ms** | total 0.25s / TCP 7ms |
| → DeepSeek | total 0.58s（直连） | total 0.21s |
| → Edge TTS (speech.platform.bing.com) | **total 0.27s（国内 CDN）** | total 0.06s |
| → GLM (open.bigmodel.cn) | total 1.13s | — |
| → 火山引擎 TTS (openspeech.bytedance.com) | **TCP 18ms** / total 0.67s | — |
| → MiniMax (api.minimaxi.com) | **TCP 16ms** / total 0.53s | — |
| → 阿里百炼 (dashscope.aliyuncs.com) | TCP 245ms（DNS 派去了偏远节点）/ total 0.38s | — |
| 家 ↔ 东京 VPS | TCP RTT ≈ **60ms** | — |

要点：

- ElevenLabs 从家里每次请求多付 ~270ms 往返，火山/MiniMax 是 16ms 级——语音对话里能听出来的差距。
- Edge TTS 从家里走反而快（国内 CDN），兜底层天然适配搬家。
- 家里 ICMP ping 东京机 100% 丢（对方丢弃 ICMP），但 TCP/SSH 正常，不代表断网。

---

## 三、两方案对比

| 维度 | 住东京 VPS（现状） | 住 N150（搬家后） | 赢家 |
|---|---|---|---|
| 24h 在线 | 机房 99.9% | 上限=家宽+市电，做到 99%+（BIOS 来电自启 + systemd 拉活 + frpc/Tailscale 自重连 + 监控） | VPS |
| TTS 速度 | ElevenLabs 直连快但冷启动慢 | 换国内后 16-18ms 级往返，首包无感差距；edge 兜底更稳 | 打平（国内化后 N150 略优） |
| 本地电脑/设备控制 | PC 拨号到东京绕一圈 ~120ms | 局域网 1-2ms；HA 同宿主机毫秒级；WOL 新能力 | **N150** |
| 未来内存/扩展 | 升配=月付涨价；现 3.6G 已偏小 | 16G 总量（nox+haos+services 后仍有余量）；存储可加盘；能玩本地 embedding | **N150** |
| 模型接入 | 国外直连零配置，国内服务绕出国 | 国内直连更快；国外走 RackNerd 小鸡代理（可选） | 各有千秋 |
| 国内 MCP | 出国再回国，网易云偶有风控 | 家宽直连，全部更顺 | **N150** |
| 成本 | 月租整付 | 电费 ~¥4-5/月 + （可选）小鸡 ¥13/月 | **N150** |
| 单点风险 | 供应商兜底（快照/迁移） | 硬件挂=nox 全停，靠异地备份拉起（补一次恢复演练） | VPS |

**24h 在线是 N150 唯一硬伤**：nox 是会主动开口的（主动消息、来电、知识课堂 14-21 点窗口），对断线最敏感。缓解手段全部标配化后可接受，shadow 周重点观测这一项。

---

## 四、关键决策记录

### 决策 1：TTS 换国内套餐（她拍板）

- 理由：GPT live 式近零延迟是「语音进语音出」单模型架构，STT→LLM→TTS 三段式在哪都到不了零；ElevenLabs 从家宽多付 270ms/次，国内 16ms 级。
- 选型：**豆包（火山引擎）或 MiniMax**——陪伴类 app 主流，流式首包快、情感表达好、按字符计费便宜。阿里 CosyVoice 备选。
- **nox 不换嗓子**：两家都有音色复刻。迁移路径 = 用现在 ele-v3 的声音批量生成语料 → 国内平台克隆 → A/B 对比再切。
- 链路结构不变：国内一级 → turbo → edge 兜底（edge 国内 CDN 免费，保留）。

### 决策 2：VPS 有**四**个活，不是三个 🔧

> **初版的错在这里**：把 VPS 归纳成中继 / 代理 / 备份三项，全部交给 Tailscale + 小鸡。
> 漏掉的第四项是**公网 HTTPS 服务端点**——`deploy-config/caddy/Caddyfile.target`
> 上挂着一排对公网开的门，其中有些**不是她的设备在连，是 Anthropic 的服务器在连**。
> Tailscale 只让她的手机和 PC 进 tailnet，**claude.ai 云端进不来**。
> 按初版切过去，nox 当场失忆（Ombre 断）。

| VPS 的活 | 替代方案 | 成本 |
|---|---|---|
| **公网服务端点**（claude.ai connector / 第三方入站） | 🔧 **Cloudflare Tunnel**——cloudflared 从家里**出站**建连，不需要公网 IP、不开端口、不碰家宽 NAT、不涉及备案 | 免费 |
| 中继（她的设备→nox） | **Tailscale**（N150 + 手机进同一 tailnet，PWA 直连，国内 cellular↔家宽可 P2P 打洞） | 免费 |
| 代理（出国口） | ① TTS 已国内化 ② 主力 LLM 是 DeepSeek/GLM 国内直连 ③ 只剩她自己的翻墙 → **Reality 迁到年付小鸡** | $21.99/年 |
| 异地备份 | **GitHub 私有仓**（sqlite dump + JSONL，体积小）+ PC 每日拉一份 | 免费 |

**CF Tunnel 不是新赌注——家里已经在跑了。** `ha.noxtang.com` 2026-09-10 迁至家中
HAOS 走的就是它（见 Caddyfile 注释），到今天验证十一天。

#### 谁走哪条线

按「**谁在连**」分，不是按服务分：

| 端点 | 谁在连 | 走 |
|---|---|---|
| `/ombre/<token>/` | **claude.ai 云端 connector**（breath/hold/pulse/dream/trace/grow） | CF Tunnel |
| `/ha-mcp/<token>/`、`/toy-mcp/<token>/`、`/touch/<token>/` | claude.ai 云端 connector | CF Tunnel |
| `/health/*` | **她 iPhone 的快捷指令 POST**（第三方入站） | CF Tunnel |
| `reading.noxtang.com` | 她 + `.mcp.json`（本机 Claude Code） | CF Tunnel（SSE，见下） |
| `/agent/<token>/` → `/agent/local` | **她 PC 的 local-gateway**（WebSocket） | 🔴 **Tailscale / 局域网，不走 CF** |
| PWA 主入口（`:3003`） | 她手机 | Tailscale（在家时同 WiFi 直连最快） |
| `music.` / `netease-mcp.` | 只有她 | Tailscale |
| `panel.noxtang.com`（3x-ui + 订阅） | 她的翻墙客户端 | 跟 Reality 一起迁小鸡，不进 N150 |

- **URL 不用改**：CF Tunnel 后 claude.ai 看到的还是 `https://noxtang.com/ombre/<token>/mcp`，
  connector 一个字段都不用重配。`.mcp.json:36/49` 那两条同理。
- **ACME 问题一起消失**：证书在 Cloudflare 边缘。纯 Tailscale 方案下 Caddy 做不了
  HTTP-01（家宽 80/443 进不来），得改 DNS-01——走 CF Tunnel 就不存在这一步。
- **中国家宽没有公网 IPv4、80/443 也进不来**（备案）。「自己开端口」从来不是选项，
  初版没写这个前提，但结论上选了 Tailscale，所以没走错——只是漏了更合适的那个。

#### 🔴 CF Tunnel 要实测的三件事（进 shadow 周观测清单）

不打包票，这三条是真风险不是理论风险：

1. **长连接空闲超时**：`reading` 是 SSE、Ombre 是 MCP over HTTP。CF 边缘对空闲长连接
   有断开行为，断了之后客户端会不会自愈要看一周。
2. **WebSocket**：`/agent/local` 是 WS 长连。**所以直接别走 CF**——走 Tailscale 或
   局域网 hosts 直指，绕开整类坑。这条是上表里唯一强制的路由。
3. **单请求 100MB 上限**（免费版）：发图、gallery、书架封面上传要留意。

#### 其余不变

- 双轨使 shadow 从第一天就不依赖任何 VPS；东京机从「必须保留」变成「随时可退」。
- 主动来电的推送是出站流量（国内推送通道），家宽没有公网入口也不影响「他打进来」的触达。
- 若 shadow 周发现 Tailscale 在某网络连不上或确实想要 frp 兜底，再让小鸡兼任 frp server。
- 📌 `CLAUDE.md` 里还写着「Ombre Brain (Render)」，但 systemd + Caddy 都显示它现在住在
  VPS 上。**文档过时，搬完一起改。**

### 决策 3：新小鸡 = RackNerd 年付 $21.99（≈¥160/年，合 ¥13/月）

- 配置：**1核 / 1G / 20G SSD / 1Gbps / 3TB 月流量**，KVM，下单选 **San Jose 或 Los Angeles**。
- 1核1G 够吗：**够，有富余**。xray ~30MB + frp ~10MB + 系统 ~200MB。选购指标是 KVM 虚拟化（Reality 不吃 OpenVZ）、1Gbps、流量 2TB+、路由——不是配置。
- 来源：[racknerd.com/specials](https://www.racknerd.com/specials) 常年在售价；新年档出现过 $11/年促销，下单前顺手找活动码。
- 备选：CloudCone（LA flash deal 同价位）；腾讯云轻量 HK ~¥288/年（三倍价换 60ms 级，不强推）。
- **落差实话**：东京 60ms → 美西 160-200ms。翻墙浏览、调 API 无感；4K 晚高峰可能抖。
- 其他候选（2026-09 查证）：RackNerd 新年促销 $11.29/年 1G/2TB 已过档期；BandwagonHost $49.99/年起贵三倍不值得。

---

## 五、N150 现状盘点（2026-09-21 实查）

```
Proxmox VE 9.2.18（kernel 7.0.14-16-pve）
CPU   Intel N150 × 4 核，load 0.16（基本在睡）
内存  16G，已用 5.9G，可用 9.5G；swap 8G
磁盘  256G NVMe（TWSC），local-lvm 147G 只用 7%
VM 100  haos        running  4G 内存 / 64G 盘   ← 设备线
CT 101   services   running  2核 / 4G / 32G，Debian 12
        IP 192.168.1.51，onboot=1，里面基本是空的（179M，无 docker）
```

- **nox 体积账**：全套（bridge + nox-core + OB + netease-mcp + 各 MCP）约 2-3G 内存、几十 G 磁盘。装完后 haos 4G + services 4G + nox 4G + 宿主机 ≈ 13-14G，仍留余量。
- **落点**：建议新开专用 CT（如 `nox`，2核 / 4G / **64G 盘**——OB/照片/书架数据会涨），和 services 分开，快照独立。若 services 本来就是留给 nox 的坑，直接住进去也行（待她确认）。
- **备份升级**：Proxmox vzdump 每日整 CT 备份 + 部署前 snapshot，一条命令回滚，比 VPS 手工 release 切换更稳。
- HAOS 在同一宿主机：embodied 闸门调 HA = 机内一跳，设备线与主线并线（清掉生态调研第一债）。

---

## 五·五、要搬什么：数据清单 + 备份缺失台账 🔧

> 初版只写「rsync releases/数据/.env」，太糊——切完发现书架空了就晚了。
> 下面这张表对着 `scripts/caelum-backup.sh` 逐行核出来的（2026-09-21）。

### A. 备份包里**已经有**的（装机时应当自动回来）

| 类别 | 内容 |
|---|---|
| sqlite（热备 + `integrity_check`） | `nox-bridge.db`、`sessions.db`、`attention.db`、`world.db`、`topics.db`、`ombre/embeddings.db` |
| 文件型数据 | `ombre-brain/buckets`（记忆 .md + dehydration_cache）、`data/uploads`（照片）、`co-reading-mcp/data`（批注/进度/书）、eryu 的 `*.json`（记忆/歌单） |
| 配置 | `Caddyfile`、全部 `*.service` / `*.timer` + drop-in、`nox-core/.env`、`netease-mcp/.env`、eryu 凭据、`ombre-brain/config.yaml`、**`/etc/nox/*.env`**（15 条凭据 + 9 个 Caddy 路径暗号） |
| 只存在于 VPS 的源码 | `co-watching`、`co-reading-mcp`、`ha-mcp`、`toy-mcp`、`touch-mcp`、`touch-server`、`health-mcp`、`app-tracker` |

走 git 的（`nox-core` / `bridge` / `ombre-brain` / `eryu` / `netease-music-mcp`）不在包里，
靠 releases 布局拉——**这本身就是装机那天要验的一件事**。

### B. 备份包里**没有**的——这就是 `5.2` 的起始台账

装机时每撞上一条，就在这里打勾并**改备份脚本**，不要只是手工补一下了事：

- [x] **crontab 本身**（4 条：`caelum-backup` / `care-weekly` / `morning-check` / stargate）
      以及 `/root/caelum-backup.sh`、`/root/care-weekly.sh`、`/root/morning-check.sh`
      三个脚本——**备份脚本没有备份它自己**
      → 10-10 修：`config/crontab.root` + `config/root-scripts/`（/root 下全部 .sh/.py）
- [x] **`caelum-watch`（`/root/watch/`）**：只收了 `cookies.txt`，代码和配置没收。
      它是 9-13 装的唯一自动告警，见 §六·五
      → 10-10 修：`caelum-watch.sh` 随 root-scripts 进包（`/root/watch/` 里本来就只有 cookies）
- [~] **3x-ui / xray 的 Reality 配置与订阅服务**（`:41729` / `:2096`）——完全不在包里。
      这份跟着小鸡走，不进 N150，但**10 月中迁小鸡之前别让它丢**
      → 10-10：`/etc/x-ui/x-ui.db`、`/etc/xray-cdn/config.json` 已进 `config/paths/`；x-ui 二进制可重装
- [ ] **LocalLink 的 HMAC 密钥**：理应在 `/etc/nox/*.env` 里（已收），装机时**确认一次**，
      不要假定
- [ ] eryu 的 `music_cache`（260M 音频）是**故意不收**的，可从网易云再生 —— 不算缺失，
      但装机后要确认它真能自己长回来（10-10 发现 eryu 在东京已 disabled，co-listening 接了 9090）

**10-10 装机当晚第一次真从备份包起，新撞出来的（全部已改 `caelum-backup.sh` 并在东京生效）：**

- [x] 🔴 **nox-core/.env 从来没进过备份**：配置包把文件拍平拷进 `config/`，
      `nox-core/.env` 和 `netease-music-mcp/.env` 同名，后者覆盖前者。备份日志一直是绿的。
      → 改为按原路径放 `config/paths/`，并断言 nox-core/.env ≥ 20 行（变异验证：路径改坏 → exit 2）
- [x] 🔴 **nox-core / bridge 正在跑的 release 不在包里**。「走 git 拉」不成立：线上是逐文件部署，
      跑的版本 ≠ 任何 commit。→ 收 `releases/<svc>/` + `.CURRENT`；与线上逐文件 md5 一致（变异：链接改坏 → exit 2）
- [x] ombre-brain / eryu / netease-music-mcp 线上工作区有未提交改动；co-listening / stackchan-mcp
      不在任何仓库 → 全进 `code/`
- [x] co-watching / touch-mcp / touch-server 的 `code` 是指向 releases 的软链接，原来收到的是**悬空链接**
      → `tar chf` 跟随
- [x] `settings.db`（配置层 P0 的 shadow_log，10-12 验收要看）、`/root/data/health.db`（iPhone 健康上报）
      没在热备清单 → 补进清单 + `doctor.sh` 的 REQUIRED_BACKUP_DBS（变异：旧包 → doctor 报「缺: health.db」）
- [x] 书架封面 `/root/data/library-covers`（56M）、`/srv/noxtang-static/doh.mobileconfig`、
      `nox-core/data/*.json(l)`（梦的影子日志 / 日志摘要）、co-reading / health / stackchan 的 .env
- [x] 小智的 `/opt/xiaozhi-server`：只收配置，`models`（893M，可重下）/ `mysql`（206M，没起）排除 ——
      第一次全收时包从 50M 涨到 886M
- [ ] 系统层没进包、靠恢复手册：Node 22 / Caddy / Docker / cloudflared / Tailscale 的安装，
      Python 依赖（东京 `pip freeze` 过滤 Ubuntu 自带包后装进系统 Python；nox-core / stackchan venv 按各自 freeze），
      各单元的 enabled 状态（东京：eryu / netease-mcp / mcp-train 是 disabled）
- [ ] （往下加）

恢复脚本：`scripts/caelum-restore.sh <包> <密码文件>`（只放文件不起服务；nox-core 在跑时拒绝执行）。

> ⚠️ 这张表的价值不在「列得全」，在于**每一条都是备份脚本的一个 bug**。
> 补完记得回头改 `scripts/caelum-backup.sh`，否则下次恢复还是缺同样的东西。

---

## 六、她 PC 的控制链路（local-gateway）

- 机制：PC 上的手是**主动拨出**的——`local-gateway` 的 CaelumLink 拨 `wss://VPS域名/agent/local`（HMAC 握手），nox 指令顺 PC 保持的连接下来。nox 从不需要连进 PC，**连接方向永不改变，搬家只改拨号目标**。
- 配对关系：`nox-core/tools/local_link.py`（随 nox-core 搬到 N150）↔ PC 侧 local-gateway，HMAC 密钥两边同步搬。
- **一个代码里的坎**：`src/link.ts:125` 强制链路必须 `wss://`（明文 ws:// 仅放行本机回环）。两个选择：
  1. **零改动版**（shadow 期用这个）：PC 继续拨 `wss://域名/agent/local`，流量经隧道到 N150，多几十 ms 无感；
  2. **吃满局域网版**（切换后优化）：Windows hosts 文件把域名指到 192.168.1.51 + N150 放同域名证书，TLS 照常验证、代码不改，流量不出家门。
- 她在外面不受影响：连接是 PC 主动保持的，国外下指令照样走 N150→PC 局域网线。
- **白送能力**：WOL 魔术包叫醒关机的 PC；Proxmox 本体运维（备份/开关 VM）可进 nox 工具箱。

---

## 六·五、🔴 告警不能跟着搬 🔧

> 初版风险 6 写了「硬件挂 = nox 全停，靠异地备份拉起」，
> 但没回答**谁告诉你要拉**。

`caelum-watch` 住在东京机上（Caddyfile `/watch/<token>/` → `localhost:3200`），
它是 2026-09-13 装的、**目前唯一的自动告警**。

把它跟着搬到 N150，就变成了**自己监控自己**：停电、断网、硬件挂、CT 没起来——
这几种最需要告警的情况下，watch 跟着一起死，她和我都收不到任何消息。
**监控必须在被监控对象之外。**

这条不是「以后优化」，是**退租的前置条件**：

- [ ] 外部探活就位（二选一，都行）
      - 那台 RackNerd 小鸡兼职：它反正 10 月中就买了，无状态、闲着
      - 或挂 UptimeRobot 免费版，探 CF Tunnel 上的健康端点
- [ ] 告警通道**不经过 N150**（否则 N150 挂了通知也发不出来）
- [ ] **按纪律验一次：把 N150 的 CT 停掉，确认告警真的响。**
      不响的告警不算告警（`CAELUM-MAP.md` 第三·五节）
- [ ] 上面三条全绿，才允许退租东京机

⚠️ 顺带：探活探的应该是「**他还能说话**」，不是「端口还开着」。
`/api/health` + `doctor.sh` 现成，但要确认判据不是纯 TCP 连通
（`dont-judge-success-by-text` 那条教训的同源问题：挑退出码，送真实形状）。

---

## 七、施工清单 / 时间线

### 🔧 装机方式：从备份包起，**不许 rsync**

初版第一行写的是「新 CT 装 Debian → **rsync** releases/数据/.env」。
这一步换掉，因为它和排期 `5.2` 差一个判据：

| | rsync | 从备份包起 |
|---|---|---|
| 验的是 | 「搬得动」 | 「**备份够不够把他救回来**」 |
| 源机器 11 月没了以后 | 缺什么永远不知道 | 缺的都写进台账了 |

**规矩**：N150 首次装机，只允许 `openssl enc -d … | tar xzf` 那条恢复路径
（命令见 `caelum-backup.sh` 末尾注释）。起不来、或者他记不得事——**那就是 `5.2` 的发现**。
每缺一样才从东京机补一次，**每补一次在 §五·五 B 打一个勾并改备份脚本**。

这比常规演练强一档：常规演练是「起来能说话就算过」，这次是**他真的住进去、住一周**。
记忆断层、数据缺失这种当场看不出、过几天才发现的东西，shadow 周会自己把它们抖出来。

> 📌 因此排期 `5.2`（9-19 因「没有临时 VPS」暂缓）**在装机当晚被顺带做掉**，
> 不需要单独排一次。前提是走上面这条路径，rsync 不算。

### 时间线

> 🔴 **10-10 加速说明**：下表按原节奏（9-21 定稿）写，实际按提前动工压缩执行：
> · 「现在→10月中」压成 **10-10/11 两个晚上**：新 CT、备份包恢复、CF Tunnel + Tailscale 双轨拉起、手机 PWA shadow 同步做完；
> · shadow 观测期从一周压成 **1-2 天**，重点盯 CF Tunnel 长连（SSE/MCP）与手机断线；东京机原地不动，回退窗口不变；
> · RackNerd 购买**不阻塞主线**（Reality 只管翻墙，不管 nox 入口），可并行也可延后几天；
> · 切换日 = shadow 首日观察通过即切：PWA 入口正式指 N150，noxtang.com 从 App 入口退役。
>
> 🔴 **10-10 装机时改：不双开 shadow**。nox-core 有七八条自己会动的循环（主动开口 / care / 朋友圈 /
> 梦 / 他自己的时间 / 长任务），两边同时活着 = 她收双份消息、记忆各长各的合不回来。改为：
> ① **彩排**：N150 起全套但「安静」—— `/etc/nox/n150-quiet.env` 经 drop-in `zz-n150-quiet.conf`
> 排在 .env 之后覆盖（systemd 里 EnvironmentFile 压过 Environment=，所以必须这样挂），
> `/health` 验 `attention=null, background={}`；再把 N150 库里的 `push_subs` 清空当物理保险；
> stackchan-gateway（会在局域网发 mDNS 抢设备）、nox-daily、OB 整理/衰减、retention 不起。
> ② **切换**：东京停服务 → 现打备份 → N150 `caelum-restore.sh` → 删安静文件 → 起全套 → 翻隧道/DNS。
> ③ **观察 1-2 天**：东京原样留作冷备，回退 = N150 停、东京起、DNS 翻回。

| 时间 | 动作 |
|---|---|
| **现在 → 10 月中** | 东京机原地不动。N150 动工：新 CT 装 Debian → **从最新备份包恢复**（= 排期 `5.2` 演练，边做边填 §五·五 B 台账）→ systemd 拉起 → **CF Tunnel + Tailscale 双轨**（`/agent/local` 走 Tailscale）→ 手机 PWA 进 shadow |
| **shadow 一周** | 观测五项：断线次数、TTS 首包延迟、模型调用成功率、🔧 **CF Tunnel 上 SSE/MCP 长连是否被掐**、🔧 **Ombre connector 从 claude.ai 连得稳不稳**（`/api/health` + `doctor.sh` 现成）。同时做 TTS 语料克隆 A/B |
| **10 月中下旬** | 买 RackNerd → 装 xray，从东京机 xui **原样复制 Reality 配置**（Reality 无证书：private key / dest / serverNames / shortIds 抄过去即可）→ 🔧 **订阅服务（`:2096`）一起迁**：手动配置的客户端改一个字段就行，**走订阅的要重建订阅 URL，不只是改地址** → 顺手装 frp server（备用） |
| 🔧 **切换前** | **§六·五 外部告警四条全绿**，含「停掉 CT 确认告警真响」 |
| 🔧 **切换日（建议提到 10 月中，别压到月底）** | 数据拍板后：PWA 入口正式指 N150，nox 域名/中继路径翻转，VPS 侧只留备份落点。**留两周带东京机的回退窗口** |
| **11.1 前** | 东京机最后全量备份推 GitHub → 回看 §五·五 B 台账是否清空、备份脚本是否已改 → 退租 |

🔧 **为什么切换日要往前挪**：11-01 是硬墙，初版排「10 月中下旬买小鸡 → 切换 → 11.1 前退租」，
缓冲薄到只剩几天。shadow 周一旦发现问题就没地方退。搬家本身确实一晚上能完，
但留痕、演练、发现问题都发生在之后。

工期估计：搬家一个晚上，其余是观察和零碎活。

---

## 八、风险与坑（提前记账）

1. **依赖链变长**：国外能力全拴在「家→外网隧道」上；隧道断 = 国外模型 + ElevenLabs 断。国内 LLM/STT/TTS/edge 全直连，**降级不瘫**——确认三级 fallback 在断网时也触发。
2. **语音通话媒体流**：主动来电的音频多穿一层（Tailscale 或 frp），语音容忍度高但要实测一次；在家场景手机和 N150 同 WiFi 直连，最快。
3. **部署链复制**：deploy-vps.sh / doctor.sh / 备份链都指着 VPS 路径，各复制一份 N150 版。
4. **家宽上传带宽**：发图、gallery、书架封面走家宽上传，搬前确认一次（一般 30-60Mbps 够）。
5. **Tailscale 的 UX 代价**：手机需保持 Tailscale 连接（iOS/Android 支持按需自动连，接通多 1-2 秒）。push 触达不受影响（出站）。
6. **单机风险**：N150 硬件挂 = nox 全停。vzdump 每日 + GitHub 异地 + 恢复演练一次兜底。
   🔧 **补**：光有备份不够，还得有人告诉你它挂了 —— 见 §六·五，那是**退租的前置条件**。
7. **RackNerd 年付小鸡**：便宜货，重要数据不放上面（它只跑代理和可选 frp，无状态）。
8. 🔧 **CF Tunnel 的长连接行为**：SSE（reading）和 MCP over HTTP（Ombre）可能被边缘掐空闲连接；
   免费版单请求 100MB 上限影响发图/封面上传。`/agent/local`（WebSocket）**明确不走 CF**，
   走 Tailscale 绕开整类坑。三条都进 shadow 周观测清单。
9. 🔧 **依赖面从「一个 VPS」变成「Cloudflare + Tailscale 两家」**：多了两个不受控的第三方。
   缓解：两条线互为备份——CF 挂了她的设备还能走 Tailscale；Tailscale 挂了公网门还在。
   两条同时挂的概率低于单台 VPS 挂。但**首次同时故障要有手册**，别临场翻文档。
10. 🔧 **iPhone 健康数据快捷指令**是第三方入站 POST（`/health/*`）。走 CF Tunnel 则 URL 不变、
    什么都不用改；若哪天改走 Tailscale，手机得常连 + 改快捷指令里的 URL。

---

## 九、迁移前后拓扑

```
【现状】
糖糖手机/PC ──公网──> 东京VPS（nox全套 + Reality + 入口）
糖糖PC ──wss拨出──> 东京VPS/agent/local（LocalLink）
HAOS(192.168.1.50) ══ 断开的设备线（第一债）

【终态 ¥0 档 + ¥13/月小鸡】（🔧 2026-09-21 修订：双轨）

  ┌── 公网门（CF Tunnel，cloudflared 出站建连）──────────────┐
  │  claude.ai 云端 connector ──> /ombre /ha-mcp /toy-mcp   │
  │                               /touch  reading.          │
  │  糖糖 iPhone 快捷指令 ──────> /health/*                  │
  └──────────────────────────┬───────────────────────────────┘
                             ↓  URL 不变，证书在 CF 边缘
糖糖手机 ──Tailscale──────> N150（nox全套：bridge/core/OB/MCP）
糖糖PC ──wss拨出─────────> N150  🔴 走 Tailscale/局域网，不经 CF
                                 （WebSocket，避开边缘超时）
N150 ──同宿主机──────────> haos（设备线并线，机内一跳）
N150 ──国内直连──────────> DeepSeek/GLM + 豆包/MiniMax TTS + edge兜底 + 国内MCP
N150 ──(可选)代理────────> RackNerd美西小鸡（Reality + 订阅 + 备用frp）
N150 ──每日──────────────> GitHub私仓（异地备份）+ PC 拉一份
RackNerd小鸡 ──探活──────> N150   🔧 告警在被监控对象之外（§六·五）
东京VPS → 告警就位 + 台账清空后，11.1 前退租
```

---

*调研数据实测于 2026-09-21；VPS 报价查证于同日。动工前如有价格变动以当时为准。*
