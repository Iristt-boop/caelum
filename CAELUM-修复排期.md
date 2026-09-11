# CAELUM 修复排期

> 面向一个人 + 兼职时间的排期。**这不是按模块排的，是按依赖顺序和"能不能一次做完"排的。**
> 证据与完整分析见 `CAELUM-架构审计-2026-09-10.md`（每条都带 `文件:行号`）。
> 用法：做完一条就把 `[ ]` 改成 `[x]`。**没到"完成判据"就不算做完** —— 判据是给你自己看的，不是给我看的。

**总时长预估：约 3 个月**（不是 3 周）。中间被打断没关系，每条线都能独立收尾。

**两条基本纪律**
1. **一次只开一条线。** 并行 = 半途而废。
2. **下次想加新能力时，先问"它属于哪条线"。** 答不出来就先不做 —— 这是唯一能让这份清单不再变长的规矩。

---

## ⚠️ 优先级修订（2026-09-10 业主实测后）

> 你去实测了四条，**推翻了我原来的第一优先级**。修订见 `CAELUM-架构审计-2026-09-10.md` 顶部的「优先级修订」。
> 一句话：**仓库是私有的、bridge 有 token、安全组挡住 11/12** —— 所以"外部人现在就能利用"的只剩一条。

| 原排序 | 修订后 | 为什么 |
|---|---|---|
| — | **新增 0.0 `9333` 收口（今晚）** | 唯一一条陌生人匿名可达的（`/latest` 200 + 1214 字节 + 零鉴权） |
| **0.2** 核实备份源路径 | **✅ 划掉** | 查完了：路径是对的，`buckets` 里 249 个桶，`ombre-brain-data` 不存在 |
| 0.4 密钥轮换 / 0.5 git 清理 | **降级为"这周"** | 仓库私有 → 不再是"今晚"。但**仍要做**：OpenRouter 那把在 master 上，且这项目历史上真发生过 `NOX_TOKEN` 公网裸奔 |
| 0.6 绑定收口 / 0.7 卫星鉴权 | **降级为正常节奏** | 安全组已挡 11/12。仍要做（纵深防御：哪天安全组被改就全开，而代码层没有任何兜底） |
| 0.8 `/uploads` | **往下排** | 文件名是 `Date.now()` + 6 位 UUID（16.7M），猜不出。真实性质是"一旦 URL 漏出就永久收不回"，不是敞开的门 |
| 0.1 备份引号 / 0.3 恢复演练 / 0.9 fail-closed / 0.10 资源上限 | **不变** | 不受影响 |

**额外收获（你顺手挖出来的）**：线上 unit **没设** `OMBRE_BUCKETS_DIR`，而**仓库里的 unit 设了** —— 这是"仓库与线上漂移"的活样本。**这条直接加强 4.8（部署版本化）的理由**，我已经写进去了。

---

## 本轮进度（2026-09-11）

> ⚠️ **本轮在线上做了实事。** 全部有备份、可回滚；备份后缀都带日期。

### 第四批（4.8 / 方向乙：配置仓库 + 密钥外置）

**① 密钥外置：15 条凭据从 unit / drop-in 搬进 `/etc/nox/*.env`（600，目录 700）**

| 服务 | 搬走 | 备注 |
|---|---|---|
| `bridge` | 7 条（unit）+ 4 条（3 个 drop-in） | 顺带**删掉 2 条陈旧重复**（`ELEVENLABS_API_KEY`/`ELEVEN_KEY` 在 unit 和 `eleven.conf` 里值不同，实测**生效的是 drop-in 那版**） |
| `ha-mcp` | 1 条 | |
| `toy-mcp` | 1 条 | 变量名叫 `NOX_TOKEN`，装的其实是**玩具专用钥匙**（= bridge 的 `NOX_TOY_TOKEN`）—— 名字有误导，设计是对的 |
| `touch-server` | 1 条 | |
| `ombre-brain` | 2 条 | 从 `service.d/secrets.conf` 搬出来 |

**验证方式（两轮都是这个标准）**：动手**前**取运行中进程 `/proc/<pid>/environ` 的凭据指纹 →
搬完重启 → 再取一次 → **逐条比对**（含"少了谁/多了谁"）→ 不一致就自动回滚。
结果：**四个服务全部指纹一致，行为零变化**。

**② 配置仓库建立**：`D:\claude-code\deploy-config\`（独立 git 仓库，已被 caelum 的 .gitignore 排掉）
- `af4f092`，**40 个文件**：17 个 unit + 8 个 drop-in + `Caddyfile.target` + crontab + env 模板（只有变量名）+ README
- `.gitignore` 挡 `*.env` / `*.secret` / `*.key` / `*.pem`
- **提交前扫了四遍**（导出时在 VPS 上扫一次、本地工作区扫一次、提交前扫一次、已提交内容再扫一次）——全部零密钥

**③ 🔴 我自己出的一次事故（已完全回滚）**

我把 `git init -q 2>&1` 写进了脚本 —— **PS 5.1 把 `2>&1` 当参数传给了 git**，
init 静默失败，于是后面的 `git add -A` 一路向上跑进了**父仓库**，
并且提交了进去（261 个文件，含 `scratch/**`、`.zcode/plans/`、6 个嵌套仓库）。

- **没有 push**，`git reset --mixed HEAD~1` 完全回滚（HEAD 回到 `6261da5`，工作区 174 项恢复原状）
- 密钥目录 `secrets/` 和 `deploy-config/` 被 gitignore 挡住了，**没进去**
- 教训两条：**① 每个 git 命令都要查 `$LASTEXITCODE`；② 别在 git 命令后加 `2>&1`**

**④ 密钥扫描救回来的两处**（这是它存在的意义）
1. 导出 `eryu.env.example` 时 —— `/root/eryu/server/.secret` 是**裸 token**，不是 `KEY=value`，
   第一版 sed 只替换 `NAME=...`，把真 token 原样导出了 → 指纹扫描抓住
2. 我在 README 里把真实的 `/agent/<token>/` 当例子写进去 → 提交前扫描抓住

**⑤ 还没做的**
- ~~**线上 Caddyfile 还是明文暗号**（6 个 hex + 1 个 agent token）~~
  → **已完成（见第七批 ①）**：实际是 9 个暗号，全部外置到 `/etc/nox/caddy.env`
- ~~`deploy-config` 还**没有 remote**（要你建一个私有仓库）~~
  → **已建并推送**：<https://github.com/Iristt-boop/caelum-deploy-config>
- 其余 11 个服务还没铺 release 布局（普查结论见第七批 ⑥）

### 第五批（2026-09-11 夜：174 项入库 + 全历史密钥清除 + VAPID 搬出源码）

**① 174 项未提交改动全部入库** —— 分成 7 个提交，不是一坨。顺带抓到三件漏网的：

- **4 个敏感文件还躺在 git 索引里。** `.gitignore` 只管"新增"，不管"已跟踪"：
  三个 tar.gz（明文 DeepSeek/Gemini key + **记忆数据本身**）和 `breath-output.txt`
  （26.8KB 真实记忆）一直还在被跟踪 → `git rm --cached`（**磁盘上的文件一个没删**）
- **整个仓库没有 `.gitattributes`。** `core.autocrlf=true` 而没有属性文件，
  磁盘上现在是纯 LF **全靠运气** —— 一次 `git checkout .` 或重新 clone 就会把
  `deploy-remote.sh` 变成 CRLF，传上 VPS 交给 bash 时报 `$'\r': command not found`，
  **而且是在软链已经翻完之后**。现在锁死了（脚本 LF、`.cmd`/`.ps1` CRLF），零改动量。
- **我自己那份审计文档里漏了一个真 token**（`/touch/` 那条）—— 因为之前的指纹扫描
  只查 40 位以上的 hex，32 位的漏了。**工具的参数选窄了，就等于没查。**

**② 全历史密钥清除（`git filter-repo`，已完成并强推）**

先做了一轮完整的历史取证，把范围收到确定，而不是"扫到什么算什么"：

| 凭证 | 位置 |
|---|---|
| HA 长期令牌（JWT） | 5 个被跟踪的 `scratch/tmp_*.sh` |
| OpenRouter key | `vps-scripts/update-bridge-env.py` 等 |
| 4 个 Caddy 路径暗号（48 位 hex） | `touch-mcp/Caddyfile` |
| bridge `X-Nox-Token`（48 位） | `scratch/tmp_chat_debug.sh` |
| `/touch/` 路径 token | `touch-mcp/Caddyfile` + `PROJECT.md` + 审计文档 |
| 高德 API key | `scratch/tmp_gaode_test.sh` |
| 家庭 WiFi 名 + 密码 | 2 个 fsr402 文件 + `PROJECT.md` |
| VAPID 公钥 / **私钥** | `bridge/server.js` + PROJECT.md / HANDOFF |
| 三个 tar.gz（含 key + **记忆数据**） | `vps-scripts/`（按路径整条从历史移除） |
| `breath-output.txt`（26.8KB 真实记忆） | 同上 |

**有意不动的两处**：`1809e…`/`c7bf1…`/`456c4…` 是**会话 ID 不是凭据**，
而且 `1809e…` 嵌在 `nox-core/tests/test_appraisal_llm.py` 里当测试夹具 ——
盲扫 32 位 hex 会把测试打坏。PROJECT.md 里公钥的 16 字符前缀也留着：
那行自己就写着"公钥，公开无妨"。

**三重验证**：
1. 重写后的 **HEAD 树与重写前逐字节对比：只有 4 个文件各变 1 行**，全是「值 → `REDACTED-*`」
2. 全 154 个提交重扫 9 类密钥形状 → **全部 0 处**
3. 实测旧 HEAD `f5220e4` 在 GitHub 上 **`git fetch` 报 `not our ref`** ——
   远端对象库里已经不可达。**所以不需要删库重建。**

备份：`D:\caelum-git-backup-20260911.git`（196MB mirror clone，重写前的完整历史）。
**留着它** —— 轮换全部做完之后里面的旧值就没用了；在那之前它是唯一的退回路径。

教训一条：`git status` 说文件改了、`git diff` 说没改 —— 是**索引里缓存的 stat 过期**
（我把 CRLF 归一化成 LF，字节数 192968 → 188936）。用
`git hash-object --path=<文件> <文件>` 跟 `git rev-parse HEAD:<文件>` 对哈希就能定死，
再 `git add` 一下刷新缓存。**别急着以为内容真的坏了。**

**③ VAPID 私钥搬出源码（审计里的 High，`PROJECT.md` 记"待修"记了很久）**

查线上时发现比记录里更糟：`/etc/nox/bridge.env` **根本没配 VAPID** ——
那段硬编码不是兜底，是**唯一来源**，她的推送一直靠它。
所以顺序不能反：先把值写进 env（600，留 `.bak-vapid-20260911-191014`）→ 再删代码。

现在源码只从 env 读、**不留兜底**；缺了不静默降级（启动打 ERROR、关掉推送、
`/api/push/vapid` 和 `/api/push/subscribe` 回 503）。回一个空 key 会让 App
订阅成功但永远不响 —— 那种坏法比直接报错难查得多。`/health` 现在也报 push 状态。

**行为零变化**：env 里填的就是原来那对值，`/api/push/vapid` 返回的公钥
**逐字节一致（87 字符）**，3 条订阅全在，她不用重新授权。bridge 97/97 测试通过。

⚠️ **仍然要轮换（0.6）**：这把私钥在 git 里躺了很久，写进 env 只是止损。
轮换会让所有推送订阅失效，要在 App 里重新授权通知 ——
生成新密钥对 → 写 env → 删兜底 → App 重授权，**顺序不能反**。

**④ 部署账本修正**：重写让所有 commit hash 变了，`CURRENT` 里记的 `f5220e4` 已经不存在于 git。
重发了一次 bridge（`2026-09-11-15e49d1466d6`）—— 内容一个字节没变，
只是让"线上跑的是哪个 commit"这句话重新成立。这正是 4.8 要解决的问题本身。

**⑤ 还没做的**
- ~~**OpenRouter 旧 key 还是活的**（实测 200）~~ → **已解决（见第六批）**：她删了，复验 401 已吊销
- 线上 Caddyfile 还是明文暗号（见第四批 ⑤）
- `deploy-config` 已推到 `Iristt-boop/caelum-deploy-config`（`af4f092`，40 文件，无未推送）
- 其余 11 个服务还没铺 release 布局
- 🔴 **`ob-tools/audio/` 在 git 里占 177MB**（109 个 mp3）—— 这是仓库 182MB 的全部原因。
  建议搬出 git（留磁盘 + 备份），但那是删内容，**等你拍板**
  → **已解决（见第六批）**：查清是共听的分析输入后移出，仓库降到 3.8MB

---

### 第六批（2026-09-11 深夜：共听的歌移出 git + VAPID 轮换 + GitHub 保留初始提交）

**① 共听的歌移出 git：仓库 196MB → 3.8MB**

先查清 `ob-tools/audio/` 到底是什么才敢动手。它是共听第 ④ 层「Music Intelligence」
的工作目录，**跑在糖糖电脑上**（VPS 没装 librosa —— 那台机器再塞个吃满 CPU 的音频分析会拖累对话）：

| 内容 | 数量 | 大小 | 是什么 |
|---|---|---|---|
| `*.mp3` | 49 | **176.5MB** | `fetch_playlist.py` 下回来的**分析输入**，能重新下 |
| `*_preanalysis.json` | 49 | 41KB | BPM / 调性 / 分段能量 —— **Nox 真正读的就是它** |
| 5 个脚本 + 1 张调试图 | 6 | ~890KB | 下歌 → 本地并行分析 → 回传 |

关键判断（都是实测，不是推测）：
- 49 个 mp3 和 49 个 json **一一对应、零孤儿** → 分析早跑完也 sync 过了
- `nox-core/tools/eryu.py:768` 读的是 **eryu 服务自己的缓存目录**，不是你电脑这个目录
  → 移走不影响 Nox 读到的任何东西

所以：**mp3 移出 git**（磁盘上一个没少，动手前做过逐字节备份 + SHA256 清单），
json 和 5 个脚本留着。第二次 `git filter-repo` 从**全部历史**清掉 → `.git` **182.5MB → 3.8MB**。

**② 两个坑 —— 都不是「代码 bug」，是「工具用错」**

🔴 **僵尸工作树把对象钉住了。** 清完历史后 `.git` 死活不缩，而
`git fsck --unreachable` 居然报 **0 个不可达**。查到最后是
`.claude/worktrees/kind-bartik-1f9833` —— 一个废弃旧会话的工作树，
**它的 index 里还留着那 49 个 mp3**，所以 git 认为那些 blob 仍然可达、不敢回收。
把它的 index 对齐到 HEAD（`git -C <工作树> reset`）之后，75 个不可达对象立刻被回收。
**教训：删完历史先看 `git worktree list`。**

🔴 **`.gitignore` 不是合法 UTF-8。** 早先用 PowerShell 往里追加规则时，
PS 5.1 按 ANSI(GBK) 写，把一个 UTF-8 文件的后半段污染成了 GBK ——
GitHub 上那几行中文注释一直是乱码，一直没人发现。
全仓库 701 个文本文件扫过，只有这一个中招。
无损修复（前半段按 UTF-8 解、后半段按 GBK 解、重写成 UTF-8），
**规则集合逐个对比：丢失 0 个，新增 1 个（就是 mp3 那条）**。
**教训：往文本文件追加内容别用 PowerShell 的默认编码。**

**③ VAPID 密钥对轮换完成**

用 `bridge/node_modules/web-push` 本地生成新的 P-256 密钥对 → 写 `/etc/nox/bridge.env`
（留 `.bak-vapidrot-*`）→ 重启 bridge → 验证 `/api/push/vapid` 回的是新公钥 →
**清掉 3 条旧订阅**（绑在旧密钥上已经作废；留着会让每次推送 401/403）。
`/health` 的 `push: available` 正常，日志无报错。

⚠️ **糖糖要在 App 里重新允许一次通知。** 不授权只是推送不响，不影响聊天。

**④ 🔴 最重要的一条：GitHub 会保留「第一个提交」，强推清不掉它**

第一次重写后我验的是「旧 HEAD 提交拉不到」—— **那个结论本身是对的，但完全不够。**

后来我用部分克隆试了一下：

```
git fetch --depth=1 --filter=blob:none origin 5144121a…   → ✅ 成功（522 个文件）
```

**重写前的「第一个提交」仍然可达**（GitHub 服务端有个看不见的 ref 保着它）。
只要它可达，它整棵树里**每一个文件**都能被任何有仓库读权限的人按 sha 取回。实测当时能拉到：

| 旧文件 | 当时 |
|---|---|
| `ob.tar.gz`（DeepSeek/Gemini key + 记忆数据） | 🔴 能，178KB |
| `buckets-data.tar.gz`（**她的记忆数据**） | 🔴 能 |
| `breath-output.txt`（**26.8KB 真实记忆**） | 🔴 能 |
| `fsr402-wifi.ino`（家庭 WiFi 密码） | 🔴 能 |
| `tmp_check_ha.sh`（HA 长期令牌） | 🔴 能 |
| `update-bridge-env.py`（OpenRouter key） | 🔴 能 |
| `server.js`（VAPID 私钥，**中期**提交） | ✅ 拉不到 |

**为什么只有第一个提交？** 那个隐藏 ref 只保住了**根提交**，
中期提交不在它树里 —— 这正好解释了那个奇怪的结果分布（老的能拉、新的拉不到）。
不把这条想通，会误以为「重写没生效」。

**解决：删库重建。** 服务端的 ref 客户端删不掉，只有重建对象库才行。
2026-09-11 深夜完成。重建后反证：

```
10 个旧脏对象（旧根提交、旧 HEAD、三个 tar.gz、breath-output、WiFi、
              HA JWT、OpenRouter key、高德 key、共听的歌）
  → 全部「拉不到」（新对象库里物理不存在）
156 个提交 × 6 类密钥形状  →  全部 0 处
```

**教训（这条比前面所有都重要）：**
**「清历史 + 强推」不等于「远端干净」。** 验证必须做两件事，缺一不可：
1. 旧提交 / 旧 blob **能否按已知 sha 取回** —— 光看 ref 列表干净是不够的
2. **根提交要单独试** —— GitHub 有保留它的机制

**⑤ 换 VAPID 密钥对暴露出来的一个真 bug：推送「开了」但永远不响**

糖糖重新授权之后推送**还是不通**。查下来不是她操作的问题，是**两处叠在一起**：

🔴 **前端复用了旧订阅**（`nox-app/frontend/src/pages/Chat.jsx`）

```js
const existing = await reg.pushManager.getSubscription();
const sub = existing || await reg.pushManager.subscribe({ ... });
```

只要浏览器里已经有订阅就**直接复用**，完全不看它是用哪把公钥向 Apple 注册的。
重新授权时浏览器交出来的还是那条绑在**旧公钥**上的订阅 → 服务端拿新私钥签名
→ Apple 回 `400 VapidPkHashMismatch`。

🔴 **服务端把失败吞掉了**（`bridge/server.js`）

`sendPushAll` 的 catch 只处理 404/410，**其余的什么都不记**；而 `/api/push/test`
只回「订阅条数」—— 条数说的是「登记了几条」，不是「发得出去」。
两边一起显示「一切正常」。

**这就是审计里「静默失败」最标准的形状**：功能全灭，而所有能看见的地方都说正常。

修法（两侧一起）：
- **App**：订阅前按字节比对公钥，不一致先 `unsubscribe()` 再重新订阅
  （不退订直接 subscribe 会抛 `InvalidStateError`）；订完**真发一条**验证，
  把服务端的逐条结果如实显示 —— 没送出去就把原因念出来，不再假装好了
- **bridge**：每个失败都打 ERROR（status + endpoint + 推送服务给的 reason）；
  `/api/push/test` 回 `{ok, sent, failed, results}`。401/403/400 属于「配置不对」
  —— **不清订阅**（清了会掩盖问题），只有 404/410 才清

**验证（不是「她说好了」就算）**：
```
新订阅  2026-09-11T12:12:47Z   endpoint …QL0FxOO9Kes24bb6…
旧订阅  2026-09-11T12:04:33Z   endpoint …QJxgInAbJKEQRacs…
   → endpoint 变了，说明 App 真的退订并重建了，不是复用
由服务端独立发一条  →  ✅ Apple 收下，HTTP 201
```

**教训（值得单独记一条）：**
**「订阅登记成功」不等于「推送送得到」。** 凡是「换了密钥 / 换了端点」这类操作，
客户端必须有「发现本地存的和服务端给的不一致就重建」的逻辑 ——
否则用户会一直看到一个永远不响的「已开启」。

**⑥ 还没做的**
- 线上 Caddyfile 还是明文暗号（见第四批 ⑤）
- 其余 11 个服务还没铺 release 布局
- 本地两个备份等确认后再删：`D:\caelum-git-backup-20260911.git`（196MB，重写前的完整历史）
  和 `D:\caelum-ob-audio-backup-20260911\`（176.5MB mp3 + SHA256 清单）
- WiFi 密码没换（她的选择）

---

### 第七批（2026-09-11 深夜续：Caddy 暗号外置 + 全仓库盘点 + 一条把顺序讲反的教训）

**① Caddy 的 9 个明文暗号搬出 Caddyfile（完成，行为零变化）**

一共 9 个：`/ombre/ /tracker/ /toy-mcp/ /ha-mcp/`（4×48 位 hex）、`/touch/`（32 位）、
`/watch/`（24 位）、`/agent/`（24 位字母数字）、`panel.noxtang.com` 下两条（2×16 位）。

现在住 `/etc/nox/caddy.env`（0600 root:root），systemd drop-in
`EnvironmentFile=/etc/nox/caddy.env` 只是**必需形式**（前面不加 `-`）。

**关键发现（让这件事能无痛做到）**：`ExecReload=/usr/bin/caddy reload ...` ——
systemd 会把 `Environment*=` 应用到**所有** `Exec*`，包括 `ExecReload`。
所以 `systemctl reload caddy` 就能拿到新变量，**不需要 restart、不断连接**。
没确认这一点的话，就会去做一个会断线的 restart。

**验证（三层，一层比一层强）**：

    caddy adapt 前后           4309 字节 → 4309 字节      逐字节相同
    运行中配置快照前后         12388 字节 → 12388 字节     逐字节相同
    空变量 / 未解析占位符      0 处
    /etc/caddy/Caddyfile      明文暗号 0 行（权限仍 644，因为已经不含凭据）
    8 份带明文的旧备份        → /root/caddy-backups（0700，文件 600）

**② 顺手抓到 3 个「正则比数据窄」（今天第 3、4、5 次同型）**

- `export-config-remote.sh` 的脱敏只覆盖 `/<服务>/<hex>/` 和 `/agent/<tok>/`，
  **漏了 panel 那种 `handle /<16位hex>/`**；而它的**残留检查用的是同一个窄正则**
  → 它报「残留 0」的时候，那 2 个 panel 暗号正躺在 `deploy-config` 仓库里
- 同一个脚本第 [5] 步的 `s/^([A-Z_]+)=.*/`：**`[A-Z_]+` 不匹配数字**，
  而变量名是 `CADDY_TOKEN_PANEL_SUB_B64` → 那一行的值原样导出。
  **这个是被第 [6] 步的指纹扫描抓回来的** —— 扫描器是今天唯一真正救过场的那道网
- `doctor.sh` 的服务清单里**没有 `caddy`** —— 整套系统唯一的入口，体检从来不查它

**检查的正则比要查的东西窄，等于没查。**

**③ 我搞错的一件事**

我说过「release 目录只增不减，该加保留策略」。**错的。**
`deploy-remote.sh:151-161` 里早就有（`KEEP=5`，且永不删当前指向的那份），
`/root/releases` 现在 6 个目录 = bridge 5 个（正好 KEEP）+ nox-core 1 个，**一直在正常工作**。
**没读那段代码就断言了。**

**④ 我今天亲手制造的回归：密钥搬了家，备份没跟着走**

备份脚本的配置清单里明确列着 `/etc/caddy/Caddyfile` —— 那是暗号**搬之前**住的地方。
而今天我做的两件事恰好把它挖空了：

  · 15 条凭据从 systemd unit 搬进 `/etc/nox/*.env`
  · 9 个 Caddy 路径暗号搬进 `/etc/nox/caddy.env`

`/etc/nox/` **从来不在备份清单里**。所以搬完之后：VPS 一没，恢复出来的是
**一份没有暗号的 Caddyfile 模板 + 一堆指向空变量的 unit**。

> **「把密钥挪到更安全的地方」和「备份跟着改」是同一件事的两半。**
> 只做前一半，比不做还难查 —— 原来的密钥至少还躺在一个被备份的文件里。

一起修的还有：**8 个服务的源码只存在于 VPS，且不在任何 git 仓库里**
（`co-watching / ha-mcp / toy-mcp / touch-mcp / touch-server / health-mcp /
app-tracker` + `co-reading-mcp` 的代码），其中包含**糖糖的接触记录**
`touch_moments.jsonl` —— 既不在 git 也不在备份。

**恢复演练（真解密真解包，不是看清单）**：

    解出 1216 个文件 / 33M
    config/nox-env/caddy.env   9 个暗号全在、零空值        ✅
    config/Caddyfile / bridge.env / 六个库 / ombre-buckets / uploads  ✅
    code/ 八个服务源码 + touch_moments.jsonl               ✅
    包体积 16M → 17M（几乎没涨）

**⑤ 🔴 最重要的一条：轮换 > 改历史（我前面把顺序讲反了）**

今天查 nox-app 的时候才想明白。我前面一直把「删库重建」当解法，但实测证明了两件事：

**1. 在 GitHub 上，「重写历史 + 强推」永远不彻底。**
重写完，**旧的根提交仍然可达** —— `git fetch --filter=blob:none origin <旧根>` 成功，
整棵树 522 个文件全在。caelum 这样，`caelum-deploy-config` 也这样（实测隔了 20 分钟仍可拉）。
「改历史」这条路本身是漏的，它在跟 GitHub 的对象保留策略赛跑。

**2. 真正的解毒剂是轮换那个凭据。** 值一作废，历史里那些字节就是废纸。

所以 SOP 应该**反过来**：

    发现某个凭据进过 git
      → ① 先测它还活不活着（拿真值打端点看 HTTP 码）
      → ② 活着就吊销 —— **这一步才是修复**
      → ③ 历史清理是次要的；而且要做就删库重建（重写 + 强推不够）

我在 nox-app 上正好做反了：先翻的历史、后测的活性。
**如果那两把 key 早就死了，根本不需要讨论重建仓库。**

**⑥ 全仓库盘点（本机 21 个 + VPS 3 个）—— 这张表本该早点做**

| 仓库 | 提交 | 结论 |
|---|---|---|
| `caelum`（根） | 161 | ✅ 今天重建过，零命中 |
| `Iristt-boop/Claude`（Ombre-Brain origin） | 7 | ✅ 9/05 那次清理**确实落在 GitHub 上了**：main 就是「数据出库：buckets 不再进代码仓」那条 |
| `caelum-room` | 5 | ✅ 零命中 |
| `ai-fishing-game-mcp` | 11 | ✅ 零命中 |
| `/root/ombre-brain` | 8 | ✅ 零命中 |
| `/root/netease-music-mcp` | 36 | ✅ 零命中（3 项未提交是漂移，不是泄露） |
| `/root/eryu` | 1 | ✅ 零命中（5 项未提交） |
| 12 个第三方克隆 | — | 不是她的仓库，不用管 |
| **`nox-app`** | 336 | 🔴 `backend/.env` 里 **2 个 API key**（comeu.ai / lmuai.com）在 `origin/main` 上**直接可达** |
| `caelum-deploy-config` | 2 | 🔴 2 个 panel 路径暗号在旧根提交里 |

**nox-app 的处理**：先测活性 → **两把都还活着（HTTP 200）** → 她已吊销（复验 401）→
历史清理降级为可选（值已作废，336 提交 × 3 分支的重写不值得）。
教训：**先测活性再决定动不动历史。**

**⑦ 还没做的**
- **其余 10 个服务还没铺 release 布局**（普查已做完，见 ① 的清单；
  `mcp-trends`/`mcp-train` 走 npx 无源码目录，`nox-daily`/`caddy` 不适用）
- ~~`caelum-deploy-config` 等糖糖删库重建后重推（本地已备好：`6e58378`，42 文件，零明文）~~
  → **已完成**：删库重建后推到 `b7ebaa7`（42 文件，零明文）；
  旧根提交 `af4f092` **复验「拉不到」**，对照组当前 master 拉得到（说明测试本身有效）
- nox-app 历史清理（可选，值已作废）
- 网易云凭据 35 天了（doctor 在提醒）
- WiFi 密码没换（她的选择）

---

### 第八批（2026-09-12 凌晨：release 布局铺到第 3–5 个服务 + 自证回滚真的会滚）

**① 铺完三个 —— 现在 5 个服务在 release 布局上**

| 服务 | 它为什么安全 |
|---|---|
| `touch-server` | 数据目录 `TOUCH_DATA_DIR` 由 unit 显式指定，且落在 release 目录**外面** |
| `co-watching` | 所有数据路径都是绝对的（`DATA_DIR`/`COOKIES`/`LEDGER`/`/tmp/watching-*`）；**WorkingDirectory 故意留在 `/root/co-watching`**，不指 code —— 以后有人加相对路径也不会跟着 release 跑掉 |
| `touch-mcp` | 自己不存数据，读的是 touch-server 那份 jsonl（绝对路径） |

每个都是同一套动作：**先造一份和线上逐字节相同的 release 0 → 翻软链 → 验证数据没动 →
再用 git 部署**。判据是**数据文件 md5 + 数据子目录清单**，不是「服务起来了」。

**② 给两个服务补了 `/health`**

`co-watching`（FastAPI）和 `touch-mcp`（FastMCP/Starlette）原来都没有健康端点，
而 `deploy-remote.sh` 要求 200，否则回滚。

两个 `/health` 查的都是**数据目录在不在**，不是「服务活着吗」——
真出问题的地方是数据路径指到别处，那时服务照常起、照常 200，只有数据不见了。
**「服务器活着」恰恰是那个陷阱最难查的地方。**

（特意不查数据**文件**本身：要等她摸过娃娃才有，查它会让全新安装永远不健康。）

**③ 自证：回滚是真的会滚**

这是整套机制的价值所在，不验就是空话。用一个隔离测试证明：

```
造一份「能编译、能启动、但 /health 必 503」的代码 → 手动跑 deploy-remote.sh

  [3] 软链 → zz2-test-rollback
  [4] 🔴 健康检查没过（unit=active health=503）—— 自动回滚
      回滚到 2026-09-11-9ed8924c6902，unit=active
  退出码 1    耗时 47 秒（重试 20 次才放弃）
  核对：软链翻回原来那份、unit active、/health 200
```

**④ 我在这轮里弄坏了一次线上，也顺手撞出一个真坑**

测试**第一次设计错了**：`sed` 改的是代码里的**默认值**，而 unit 的
`Environment=TOUCH_DATA_FILE=...` 覆盖了它 → `/health` 照样 200，什么也没测到。
（这本身是个好消息：显式 env 挡住了坏默认值。）

然后清理时我**把软链正指向的那个 release 目录删了** → `touch-mcp` 的 `code` 变成断链。
立即修好并复验。

但它暴露的坑值得做成守卫：**断链的服务现在好好的**（代码已经在内存里），
**下次重启才起不来** —— 在你重启之前一个字都不表现。已加进 `doctor.sh` 第 9 节，
实测能叫出来（故意断链 → 明确报出服务名和错误指向）。

> `deploy-remote.sh` 自己的 KEEP 清理是**保护当前指向那份**的
> （`case "$(readlink -f "$LINK")"`），所以正常流程不会产生断链；会断的是人手动删。

**⑤ 删掉两份「上膛的枪」**

`fsr402-touch-server/touch-server.service` 和 `co-watching/co-watching.service` ——
都是**第三份副本**（线上 / deploy-config / 代码仓），路径还是 release 布局之前的，
而且会被 `git archive` 打进 release 目录，**一个 `cp` 就能把配置改回去**。

touch-server 那份尤其危险：**它没有 `EnvironmentFile`（所以没有 `TOUCH_TOKEN`），
而代码是 fail-open 的** —— 谁部署了它，她的接触记录就重新敞开。

**⑥ 顺带堵掉审计的 Critical：touch-server 的 fail-open**

```python
TOUCH_TOKEN = os.environ.get("TOUCH_TOKEN", "")
if TOUCH_TOKEN:   ...校验...          ← 空值整段跳过 = 鉴权关闭
```

改成：**没配就拒绝启动**（跟 bridge 的 `NOX_TOKEN` 一个道理）。
本机测试留**显式**逃生门 `TOUCH_ALLOW_NO_AUTH=1`（而不是靠"忘了设"）。
实测：不设 → exit 1；设了 → 正常起、`/health` 200。

**⑦ 还没做的**
- 其余服务铺 release 布局：`app-tracker`、`ha-mcp`、`toy-mcp`、`health-mcp`
  （`ombre-brain`/`eryu`/`netease-mcp` 在 VPS 上本来就是 git 仓库，优先级低；
  `mcp-trends`/`mcp-train` 走 npx 无源码目录；`nox-daily`/`caddy` 不适用）
- **`co-watching` 的 3200 绑在 `0.0.0.0`**（审计 0.6 绑定收口）——
  Caddy 只反代 `/watch/<token>/`，但它自己是敞着的，安全组一变就全开
- 网易云凭据 35 天了（doctor 在提醒）
- **`eryu` / `netease-mcp` 的方向**（她 2026-09-11 说的）：想做成自己的 `co-listening`。
  我的建议是三步，别一步跳到底：
  **第一步**先正经 fork 成自己的仓库、把那 **8 项未提交改动**（eryu 5 + netease 3，
  现在只存在于 VPS 上）入库、上游挂成 `upstream` —— 这一步做完"不协调"的感觉会消失大半，
  因为那个感觉的真正来源是"我改了什么自己都说不清"；
  **第二步**再按 `co-reading`/`co-watching` 的形状重排成一个服务；
  **第三步**才考虑重写内部 —— 而且大概率不需要，因为
  **你真正在意的部分已经是你的了**（ob-tools 的音频分析、`eryu_experience`、
  按能量分档选歌、上下文接线），第三方那层主要是**网易云的管道**
  （cookie 登录 / 签名 / 反爬），重写它的收益比看起来小得多

---

### 第三批（4.8 部署版本化：机制建成 + 两个服务上线）

**机制已建成，`nox-core` 和 `bridge` 已切到 release 布局**（`deploy.ps1 -Status` 可查）：

```
/root/nox-core/
  ├── .venv/  .env  data/            ← 都不动（venv 108M、数据 24M）
  └── code -> /root/releases/nox-core/<tag>/
/root/releases/nox-core/             ← 每个 release 是 git archive 的完整一棵树
```

**工具**（都在 `scripts/`）：
- `deploy.ps1 <服务> [commit]` —— 本地入口：git archive → 上传 → 远端部署
- `deploy-remote.sh` —— 远端：解到新目录 → 验证 → **翻软链** → 重启 → 健康检查 → **失败自动回滚**
- `deploy-status.sh` —— 线上每个服务现在跑哪个版本 + release 目录 + 部署日志

**🔴 第一次迁移用"release 0 = 当前线上代码的快照"**（字节相同，行为零变化）—— 因为
**你的未提交改动已经在线上跑了**，用 git 部署会把它们回滚掉（见下）。

#### 趟平的坑（这就是"先在最难的服务上验证"的价值）

| 坑 | 服务 | 怎么发现的 | 修法 |
|---|---|---|---|
| `__file__` 相对路径 → **每个 release 会新建一套空数据库** | nox-core | 设计时推演 + 确认 `.env` 里没有 `NOX_DB_PATH` | 显式写死 `NOX_DB_PATH=/root/nox-core/data/sessions.db`（一处管 5 个库） |
| **ESM 的 `node_modules` 解析**（`NODE_PATH` 对 ESM 无效） | bridge | 写 case 表时推演 | 每个 release 建 `node_modules` 软链 → `/root/bridge/node_modules` |
| `__dirname` 相对路径（`DB_PATH` / `FRONTEND_DIST`） | bridge | grep `__dirname` | unit 里显式写死这两个 |
| venv 不能进 release（108M/份） | nox-core | 设计时 | venv 留在 `/root/nox-core/.venv`，`ExecStart` 指它 |
| **PowerShell 5.1 给 native stdin 的最后一行补 CRLF** | 本地脚本 | `head -n 20` 报 `invalid number of lines: '20\r'` | 脚本末尾补无害行；状态查询改成上传成文件再跑 |
| **`.ps1` 没有 BOM 时 PS 5.1 按 GBK 解码** | 本地脚本 | 中文乱码 + 解析崩 | `deploy.ps1` 必须是 **UTF-8 with BOM** |

**验证**：两个服务迁移后 `unit=active / health=200 / 前端=200`，**数据库字节数完全一致**（没被重建），
鉴权仍 403，端到端他真的回话了。

#### ⚠️ 下一步卡在你这儿：**先 commit，才能用 git 部署**

实测三个未提交文件的线上状态：

| 文件 | 线上 | HEAD | 工作区 | 含义 |
|---|---|---|---|---|
| `nox-core/agent/loop.py` | `744554a6` | `469ac56a` | `744554a6` | 🔴 **你的改动已在线上、没提交** |
| `nox-core/personality/scenes.py` | `28d1ea8f` | `36f255f1` | `28d1ea8f` | 🔴 同上 |
| `CAELUM-MAP.md` | —（不部署） | | | 文档 |

**如果现在就 `git archive HEAD` 部署，这两个改动会被回滚掉。** 所以：
- 你可以先 `git add` 这两个文件 + 我今天的改动，提交一次，然后 `deploy.ps1 nox-core` 才能真正开始用
- 或者接受"暂缓 git 部署"，继续手工，等你有空整理提交

（我今天的改动也全都没提交，`git status` 里能看到。）

#### 顺手发现的小毛病

`doctor.sh` 的「attention 引擎 30 分钟没动静」在**重启后必报一次假红**（慢线 900s，重启后第一轮还没到）。
我自己重启 nox-core 之后就被它骗了一次。这种"红了但没事"最伤监控可信度，值得修（重启后 30 分钟内跳过这条）。

---

### 第二批（凭据轮换 + HA 迁移连带损坏）

**凭据轮换：完成并双向验证**
| 凭据 | 新值 | 旧值 |
|---|---|---|
| HA 令牌 | ✅ 可用 | ✅ 已失效 |
| DeepSeek | ✅ 可用 | ✅ 已失效 |
| 智谱 | ✅ 可用 | ✅ 已失效 |
| **OpenRouter** | ✅ 可用 | 🔴 **仍然有效 —— 需要你再去后台确认** |

- 8 处替换，每处留 `.bak-rot-20260911`；**先拿新值去官方端点验证，验过才动配置**
- 端到端验证：他真的回话了（`POST open.bigmodel.cn/... 200 OK`）
- 🔴 **拦下一个会静默坏掉的东西**：`ombre-brain.service.d/secrets.conf` 的 `OMBRE_API_KEY` **还是旧的 DeepSeek key**（`utils.py:85` / `dehydrator.py:270` —— OB 脱水用的）。不修就直接吊销 → 日记归档/记忆 grow 静默失效
- 为此写了三个可复用工具（在 `secrets/`，已 gitignore）：`apply-rot.py` / `scan-old-keys.py` / `verify-revoked.py`

**🔴 HA 2026-09-10 迁到家里的 HAOS，留下三个连带损坏（都已修）**

1. **Cloudflare 把 urllib 的默认 UA 挡成 403** → 三处 `urllib` 调用全断，而且 403 被上层当成"这个源没数据"**静默降级**：
   - `context/providers/location.py`（HATrackerSource）
   - `api/world.py`（`ha_state_getter`）
   - `tools/http.py`（`RestClient` —— `PresenceSource` 走它）
   → **「她出门了 / 她到家了」整条主动关心线从 9/10 起静默断掉**，日志只留一句「所有数据源都没有位置数据」。
   （`httpx` 的默认 UA 不受影响 —— 所以 ha-mcp / 家电控制一直是好的）
2. **`NOX_HA_API_URL=http://localhost:8123`** 指向已关掉的 VPS HA。`config.py` 的默认值也是这个死地址，已改成留空（走"留空即不启用"惯例，日志会明说）
3. **`api/world.py` 从来没被部署过** → 线上 `server.py:1916` `from api import world as W` 直接 ImportError → **`/api/nox/world` 返回 500，World 页（户型图）是坏的**

**验证**：`/api/nox/world` 500 → **200**，`sky/home/presence` 全 `ok=true`，`presence.state="home"`，location 拉取失败计数 **0**。
本地 1542 个测试**全绿**。

**顺手清掉**
- 线上 `homeassistant` 容器 + 镜像（3.43GB）+ config（344MB）→ **磁盘 47% → 41%**
- 20 个含活密钥的 `.bak` 文件全部收紧到 600

**⚠️ 又一次「逐文件传」的代价**：这已经是今天第三处仓库↔线上漂移
（`bridge.service` 缺 `NOX_TOKEN`、`ombre-brain` unit 缺 `OMBRE_BUCKETS_DIR`、**`api/world.py` 根本没传上去**）。
→ **排期 4.8（部署版本化）现在是我认为最该做的一件事。**

---

## 第一批（同日早先）

> ⚠️ **本轮在线上做了实事。** 全部有备份、可回滚；备份路径都带 `.bak-20260911`。

### 🔴 本轮最大发现：一个审计完全没看到的活故障

**机器当时正处在"随时会再 OOM 一次"的状态**（我上 VPS 才看到，代码审计看不到）：

```
Mem:  3719 total, 2892 used, 只有 587 可用
Swap: 2047 total, 2020 used   ← 98.6%
```

根因：`mcp-trends.service`（supergateway，监听 `*:9092`）**给每个 streamable-HTTP 会话 spawn 一个 `mcp-trends-hub` stdio 子进程，会话结束后不回收**。2.6 天积了 **132 个子进程**，每个约 4.5MB RSS + 26MB swap。
（nox-core 侧 `tools/mcp_client.py:69-88` 的 `acall` 每次调用都新开一个会话 —— 注释里写"不常驻 session"，本身没错，但对面不回收就成了泄漏。）

**清理结果**：

| 指标 | 清理前 | 清理后 |
|---|---|---|
| Swap 已用 | 2038 MB | **414 MB** |
| 可用内存 | 587 MB | **2488 MB** |
| node 进程数 | 135 | **8** |

**并加了防复发**（drop-in，删掉即回滚）：
- `mcp-trends`: `MemoryMax=512M` + `MemorySwapMax=0` —— 配合 `Restart=always`，涨到上限就自动重启，**再也拖不垮整机**
- `bridge`: `MemoryMax=768M`（基线 90MB，8 倍头寸）
- `ombre-brain`: `MemoryMax=768M`
- `nox-core` 本来就有 300M（**审计说"全部 unit 无上限"是错的**，我说声抱歉）

> ⚠️ **治本还没做**：会话生命周期该修（nox-core 侧常驻 session，或换掉 supergateway）。现在只是关在笼子里。

### 线上已完成（含验证）

| 项 | 做了什么 | 验证 |
|---|---|---|
| **0.1** 备份 | `pull-vps-backup.cmd` 引号 bug + 轮转改按份数 + 心跳；`doctor.sh` 加异地检查并部署 | `OK caelum-2026-09-11-0417`（15.25MB）✅ 异地心跳已回写 ✅ |
| **0.0** touch-server | 启用 `TOUCH_TOKEN`；**给 `GET /latest` 补上门禁**（原来只有 POST 查 token，读取全公开）；修复 `touch_server.py` 并部署 | 外网 `GET /latest` → **403** ✅ 数据完好 178 行 ✅ |
| **0.9** 鉴权 | bridge **fail-closed** 部署上线 | `/api/health` 200 / 无 token 打 `/api/todo/list` **403** / 公网 200 ✅ |
| **0.8** 上传 | 扩展名白名单 + `/uploads` nosniff/CSP + STT 残留清理，随 bridge 一起上线 | 97/97 测试 ✅ |
| **0.10** 资源上限 | 见上（3 个服务加限） | `systemctl show` 已确认 ✅ |
| **0.5** 脱敏 | 15 个 vps-scripts 归档/删除、HA JWT 脱敏、WiFi 凭据外置、`PROJECT.md` 两处脱敏 | `git grep` 全部密钥模式**全空** ✅ |

### 两个我犯的错（已修）

1. **我在 `.cmd` 文件里写了中文注释** → cmd.exe 用 OEM 代码页解码，UTF-8 中文让 `rem` 行结构崩了，脚本报一堆"不是内部命令"。改成纯 ASCII 后正常。
2. **我第一次验证 touch-server 时，POST 了两条 `{}` 进她的真实数据文件**（180 行 → 多了 2 条垃圾）。已精确删除那 2 条，恢复成 178 行 / 18266 字节，原文件备份在 `touch_moments.jsonl.bak-20260911`。

### ⚠️ 你下次碰那个娃娃之前必须知道

touch-server 现在要 token 了。**没更新 `wifi_secrets.h` 就刷固件，设备会 403、数据传不上来。**
`fsr402-wifi.ino` 已经改成 `VPS_URL` 可被 `wifi_secrets.h` 覆盖，你的那份里要有：

```c
#define WIFI_SSID "你家WiFi名"
#define WIFI_PASS "密码"
#define VPS_URL "http://43.133.211.140:9333/touch/<TOUCH_TOKEN>"
```

（token 值在 VPS 的 `/etc/systemd/system/touch-server.service` 里。）

### 仍然必须你自己做的

- **0.4 密钥轮换** —— 要登各家后台。优先 **OpenRouter**（它在 master 上）
- **0.0 的收尾** —— 安全组把 9333 关掉或只放行你家 IP（现在代码层已经守住了，这是第二道）
- **0.3 恢复演练** —— 需要你确认桶数/对话数对不对
- **VAPID 四步** —— 生成新密钥对 → 写 env → 删代码里的兜底 → App 重新授权通知（顺序不能反）
- **0.6 绑定收口 / 0.7 卫星鉴权** —— 线上还有 10 个服务绑 `0.0.0.0`（安全组挡着，属纵深防御）
- `journald` 占了 **1016MB** 磁盘，想清就 `journalctl --vacuum-size=200M`

### 顺手发现的漂移（都不是这次改的）

- `bridge.service.d/` 下有 `eleven.conf`/`eryu.conf`/`ledger.conf`，`ombre-brain.service.d/secrets.conf` —— **只在线上存在的配置**
- `monitor.sh` **根本没在 cron 里**（`/var/log/nox-monitor.log` 不存在）—— 所以"监控没人看"这条其实更彻底：**它从没跑过**。而装它的 `deploy-monitor.py` 指向退役旧 IP，已被我归档

---

## 第 0 条线：止血（本周 · 3–4 个晚上 · 约 6 小时）

> **为什么排第一**：不是为了防黑客，是为了**给你接下来动手的许可证**。备份没好之前，不要碰数据库和部署 —— 不然改坏了没得回退。
>
> **一条重要判断**：先去轮换密钥，**不要先去清 git 历史**。清历史耗时、有风险，而且撤不回别人已经 clone 的那份；轮换只要几分钟就断了攻击者的路。顺序永远是 **先轮换，再清历史**。

- [ ] **0.0 收口 `touch-server:9333`（今晚 · 15min）** ← **唯一一条陌生人现在就能做到**
  `http://43.133.211.140:9333/latest` 实测 **200 / 1214 字节 / 零鉴权** —— 你的身体接触记录公网匿名可读。

  🔴 **别直接去掉 `touch-server.service:13` 那行的注释就重启** —— **会打断你的娃娃**：
  ESP32 固件里 `VPS_URL = "http://43.133.211.140:9333/touch"` 是**编译期硬编码**的（`fsr402-wifi.ino:17`），没有配置门户；而 `touch_server.py:35` 一旦设了 `TOUCH_TOKEN` 就要求 `/touch/<TOKEN>` 路径或 `X-Touch-Token` 头。设备两样都没有 → **触摸数据静默断掉，而且你不会立刻发现。**

  **两条真的 15 分钟、且不碰固件的做法（选一个）：**
  - [ ] **A（保功能）**：安全组把 9333 改成**只放行你家宽带 IP**。代价：家里 IP 变了要手动更新一次。
  - [ ] **B（保安全）**：安全组直接把 9333 关掉。代价：暂时失去触觉感知（「她摸了我」这条线断）。

  **判据**：从外网（手机流量）打 `/latest`，**不通**；如果你选了 A，`touch_moments.jsonl` 还在长。

  **另立一条**（要碰硬件，别挤在今晚）：
  - [ ] **0.0b 加 token + 重刷 ESP32**（1h，需要 USB 碰得到板子）
        改 `fsr402-wifi.ino:17` 的 `VPS_URL` → `http://43.133.211.140:9333/touch/<TOKEN>`，同时 unit 里启用 `TOUCH_TOKEN`。做完之后 A 方案的 IP 白名单就可以撤了。

- [x] **0.1 ~~修异地备份的引号 bug~~**（✅ 2026-09-11 代码已改，**待你跑一次验证**）
  `scripts/pull-vps-backup.cmd` 一共改了三处：
  1. `"%DST%\"` → `"%DST%/%NAME%"`（就是那个从 09-06 起连续失败的引号 bug）
  2. 轮转从 `forfiles /d -7`（按天数）改成**按份数保留最新 7 份**，和注释说的对上，且永不删最新那份
  3. **新增心跳**：拉成功之后回写 `/root/.offsite-ok`，`scripts/doctor.sh` 也加了对应检查
     —— 补的正是"异地那一跳死没死没人管"这个盲区（这次就是这么没人发现的）
  - [ ] **你要做的**：双击跑一次 `scripts\pull-vps-backup.cmd`，确认 `backups/vps/pull.log` 最后一行是 `OK caelum-...`，且目录里出现当天的 `.enc`
  - [ ] 之后把改过的 `scripts/doctor.sh` 传到 VPS（`/root/doctor.sh`）

- [x] **0.2 ~~核实并修正备份源路径~~**（✅ 2026-09-10 已核实：**无需修改**）
  实测 `/root/ombre-brain/buckets` 里 **249 个记忆桶**，`ombre-brain-data` **不存在** —— 路径是对的，备的是真数据。
  ⚠️ 但顺手发现：**线上 unit 没设 `OMBRE_BUCKETS_DIR`，而仓库里的 unit 设了** —— 这是"仓库与线上漂移"的活样本，已并入 **4.8（部署版本化）** 的理由。
  （保留这条只为留痕，不用再做。）

- [ ] **0.3 跑一次真恢复演练**（1h）— **这条别跳**
  decrypt → 解包 → `PRAGMA integrity_check` → 数一下桶数和对话条数，跟线上对一下。
  **判据**：你能不看文档默写出恢复步骤，并且真的恢复成功过一次。
  （背景：恢复步骤目前只是 `caelum-backup.sh` 末尾的一段注释，从未演练。）

- [ ] **0.4 轮换全部已泄露凭证**（2h）— **顺序不能反** · **降级为"这周"**
  实测仓库是私有的 → 不再是"今晚"。**但仍然要做**：OpenRouter 那把在 `master` 上；私有仓库的读权限会经由协作者 / 被钓鱼的 GitHub 账号 / 泄露的 PAT / CI 日志扩散；**而这个项目历史上真的发生过 `NOX_TOKEN` 在公网裸奔**（`PROJECT.md`）—— "曾经公开过"在这里不是假设。
  **正确顺序：配新 key → 重启服务 → 验证能用 → 最后才 revoke 旧的。** 反过来会把自己打瘫。

  轮换清单（按优先级）：
  - [ ] **OpenRouter**（最高优先）— 在 `origin/master` 上、且与本机在用 key **逐字节相同**：`vps-scripts/update-bridge-env.py:46`
  - [ ] **HA 长期令牌**（至少 2 把在役）— tracked 脚本 `scratch/tmp_check_ha.sh`、`tmp_check_ha2.sh`、`tmp_fix_zone.sh`、`tmp_ha_user.sh`、`tmp_zone.sh`
        ⚠️ **要同时改三处**：`ha-mcp` / `nox-core` / `bridge`。只改一处，灯就控不了。
  - [ ] **家庭 WiFi SSID + 密码** — `fsr402-wifi/fsr402-wifi.ino:15`、`fsr402-test/fsr_wifi.py:26`
  - [ ] **DeepSeek + Gemini** — tracked 的 `vps-scripts/ob.tar.gz` / `full-ob.tar.gz`（内含 `ombre-brain/config.yaml`）、`vps-scripts/config-tmp.yaml:8,16`
  - [ ] **智谱** — 明文在本机 PowerShell 历史里（`ConsoleHost_history.txt:185`）
  - [ ] **工作机当已失守处理**：清 PowerShell 历史、凭据移进 DPAPI/WinCred、收窄 `D:\claude-code` 的 ACL（现在是 Users 可读 / Authenticated Users 可改）、给免密 SSH 私钥加口令
  - [ ] **旧 VPS root 口令** — 历史提交 `5144121`；两台旧机（`47.84.92.71` / `47.93.219.252`）无条件改密
  - [ ] **VAPID 密钥对** — `bridge/server.js:536-537`（**留到最后做**）
        ⚠️ 换密钥会让**所有推送订阅失效**，你要在 App 里重新授权一次通知
  - [ ] **NOX_TOKEN** — `bridge/server.js` 的兜底值 + 本机 `.claude/settings.local.json`
  - [ ] **玩具 token** — `nox-app/frontend/public/toy.html:102`

- [~] **0.5 把密钥从仓库里拿走**（部分完成 2026-09-11）
  - [x] `git rm --cached vps-scripts/ob.tar.gz full-ob.tar.gz buckets-data.tar.gz`（文件留在盘上，随时可恢复）
        —— `buckets-data.tar.gz` 里是**她的记忆数据**，不只是密钥
  - [x] `.gitignore` 加规则挡住 `vps-scripts/*.tar.gz|tgz|tar|zip`（一次 `git add -A` 也扫不回来了）
  - [x] `touch-mcp/Caddyfile` 五个真实 hex → `REPLACE_ME_*` 模板 + 头注释说明真实值只留线上
  - [x] **`PROJECT.md:5122` 那条完整 URL 也脱敏了**（它才是最大的一处：被跟踪、明文、可直接点）
  - [x] `scratch/Caddyfile` 加进 `.gitignore` —— 它未跟踪但躺在工作区，一次 `git add -A` 就会把暗号扫回仓库
  - [x] 删掉 `vps-scripts/update-caddy.py`（跑一次就会写入无前缀无鉴权的 Caddyfile，**等于把记忆库的门拆掉**）
  - [ ] **还没做 —— 需要你来**（都是账号操作或要动线上）：
    - [ ] 轮换那五个路径暗号（旧值已在 git 历史里）：`ombre / tracker / toy-mcp / ha-mcp / touch`
    - [ ] 脱敏 4 类仍在跟踪文件里的明文密钥：
          `vps-scripts/update-bridge-env.py:46`（OpenRouter）、`scratch/tmp_check_ha*.sh` 等 5 个（HA JWT）、
          `fsr402-wifi/fsr402-wifi.ino:15` + `fsr402-test/fsr_wifi.py:26`（家庭 WiFi）——
          前两类直接删值改读 env/wifi 凭证；WiFi 那两个建议挪到不入库的 `secrets.h`
    - [ ] `scripts/morning-check.sh:200` 改成从 `EnvironmentFile` 读 token，不再 grep unit 文件塞进 curl argv
    - [ ] 清历史 `git filter-repo`（**放在最后**，先把 key 轮换掉才有意义）
  - **判据**：`git grep -l "eyJhbGciOi"` / `"sk-or-v1"` / `"23ad202c"` 全部为空；`git ls-files vps-scripts | Select-String tar` 为空
  - ✅ 本轮已验证：五个暗号在**被跟踪文件里已无残留**（`git grep -l` 五次全空）

- [ ] **0.6 服务绑定收口**（1h）· **降级为正常节奏**（安全组已挡 11/12；做它是为了纵深防御）
  实测只有 `nox-core:8100` 和 `health-mcp:8101/8102` 绑了回环 —— **正确做法你仓库里已经有了，只是没统一执行**。
  - [ ] 改绑 `127.0.0.1`：bridge / app-tracker / toy-mcp / ha-mcp / Ombre-Brain / netease-music-mcp / eryu / co-reading / co-watching
  - [ ] Caddy 里的 `reverse_proxy localhost:<port>` 改成 `reverse_proxy 127.0.0.1:<port>`（避免 IPv6 解析意外）
  - [ ] 🔴 **`touch-server:9333` 不要绑回环** —— ESP32 是从你家网络**直连公网 IP** 上报的，绑了就废了。它见 **0.0**。
  - **判据**：从外网 curl 那些端口，全部不通；但 Caddy 上的站点照常能开。

- [ ] **0.7 卫星层鉴权收口**（1h）· **降级为正常节奏**（同样：安全组已挡，这是纵深防御）
  | 服务 | 怎么修 |
  |---|---|
  | `touch-server:9333` | **见 0.0 / 0.0b**（它是唯一真开门的，已经不在这条线里了） |
  | `netease-music-mcp` | 删掉 `netease-mcp.noxtang.com` 站点块，或加 basic auth（它零鉴权还持有她的网易云 Cookie） |
  | `co-reading` | 设 `MCP_AUTH_TOKEN` —— 它是 **fail-open**（`server-sse.js:51` `if (!authToken) return true`） |
  | `app-tracker` / `toy-mcp` | 恢复 DNS-rebinding 防护（`main.py:11` / `:16`）+ 加 token 头校验 |
  | `co-watching` | **改短时效票** —— 它**已经被 Vite 内联进 `dist/assets/index-*.js` 了**，拿到安装包的人就有全部权限。<br>⚠️ 2026-09-12 修正：**光轮换 hex 没用**，因为这个 token 按设计就必须在客户端里（Movies 房要带着它调 URL），新 token 照样会被打进下一个包。真正的修法是让客户端向 bridge 换一张**带签名、会过期**的票（同 `musicSig` / scribe-token 模式） |
  | `ha-mcp` | 加 token；顺手修 `hass_set_state`（`main.py:168-176` 不校验 `DEVICES`） |
  **判据**：拿掉 token 之后，这些端点全部 401/403。

- [~] **0.8 上传链路加固**（部分完成 2026-09-11）
  实测：文件名是 `${Date.now()}-${randomUUID().slice(0,6)}${ext}` —— 时间戳后面挂了 6 位 UUID（16.7M 种），**实践上猜不出**。
  所以它的真实性质不是"敞开的门"，而是「**一旦某个 URL 从别处漏出去就永久无法收回**」：无鉴权 + 30 天 immutable + 无吊销。

  **已修（同源 XSS 那条链）**：
  - [x] **扩展名白名单**：原来 `path.extname(...).replace(/[^.\w]/g,"")` 只过滤特殊字符、不过滤类型，
        `.html`/`.svg` 能活着落盘，浏览器按 `text/html` 打开就能读走 `localStorage['nox-auth-token']`
        —— **一次上传 = 全系统接管**。现在只认图片后缀，不在白名单就用 MIME 猜，再不行给 `.bin`
  - [x] `/uploads` 响应加 `X-Content-Type-Options: nosniff` + `Content-Security-Policy: default-src 'none'; sandbox`
  - [x] **启动清扫残留 STT 录音**：`/api/stt` 把整段录音写进 uploads 再让 DashScope 取，靠 60 秒
        `setTimeout` 删；进程在那 60 秒里重启 → **她的录音永久躺在公网上**。现在启动时清掉超过 1 小时的 `stt-*`
  - [x] 新增 `bridge/test/uploads-hardening.test.js`（5 条：.html/.svg 落盘后缀、正常 png 不被打坏、响应头、现状钉住）

  **🔴 我写错了一处，在这里更正**：原计划里写了「顺手加 `Content-Disposition: attachment`」——
  **那是错的**，加了之后聊天里的图片会变成下载，`<img src>` 显示不出来。所以**没加**，改成用白名单 + CSP sandbox 达到同样目的。

  **还没做（要动 STT 主链路，单独排）**：
  - [ ] `/uploads` 改成签名 URL（照 `musicSig` 那套，单文件 + 短期有效）
  - [ ] STT 音频挪出公开目录，改由带签名的临时路由提供（DashScope 无 header，签名放 URL 里）
  - [ ] 加一个孤儿文件清理 cron（不只启动时扫一次）
  **判据**：不带签名访问一张照片 URL，被拒；且语音消息/通话功能照常。

- [x] **0.9 ~~鉴权 fail-open~~ → fail-closed**（✅ 2026-09-11 已改 + 已加测试）
  - [x] `bridge/server.js`：`NOX_TOKEN` 缺失 → `console.error` + `process.exit(1)`（照 ha-mcp 的做法）
  - [x] 删掉 `ensureApiAuth` 里那行 `if (!AUTH_TOKEN) return next();`（已不可达，留着会误导）
  - [x] 顺带留痕：未设 `NOX_LOGIN_PASSWORD` 时 warn（登录密码正在复用主 token 本身）
  - [x] 新增 `bridge/test/auth-fail-closed.test.js`：① 真跑一个没配 token 的进程断言它 exit 1；
        ② 结构性检查，防止 `if (!AUTH_TOKEN) return next()` 被写回来
  - [x] `node --test` 全量 **97/97 通过**（原 90 + 新增 7）
  - [ ] **你要做的**：确认线上 `bridge.service` 里真的有 `NOX_TOKEN`（仓库里那份**没有**，是另一处漂移）；
        建议顺手改成 `EnvironmentFile=/etc/nox/bridge.env`（600）
  - ⚠️ 部署后如果 unit 里漏了 token，bridge 会**拒绝启动并每 5 秒重启一次** —— 这是有意的（比静默裸奔好），
    但要记得去看 `journalctl -u bridge`

- [ ] **0.10 所有 unit 加资源上限**（1h）
  6 个 `.service` grep `MemoryMax|CPUQuota|TasksMax` **零命中**，而机器 **4GB 无 swap 且已经真实 OOM 过一次**（整机 SSH/HTTP 不通，只能去云控制台硬重启）。
  加 `MemoryMax` + `MemorySwapMax=0`；服务改成专用用户 + `NoNewPrivileges` + `ProtectSystem`；`uncaughtException` 改成 `process.exit(1)`。
  **判据**：`systemctl show bridge | Select-String MemoryMax` 有值。

---

## 第 1 条线：静默失败的解药（第 2–3 周）

> **收益最大的一条**：它同时消掉一大片 High，而且是后面所有事的信任基础。
> （背景：utility 401 跑了 30+ 小时没人发现，因为"表面上他好好的"。）

- [ ] 1.1 `/chat` 加请求级 deadline（180s）+ `limit_concurrency=8`；`max_retries` 4→2（半天）
      （现状：单请求最坏 90 分钟、无并发上限，一个半死 provider 能让 Core 整体不响应）
      **判据**：打一个假死的上游，Core 不会整体卡住。
- [ ] 1.2 `agent/loop.py` 加结构化 turn 日志（outcome / iterations / tools / model / tokens）（半天）
      （现状：整个 agent loop **只有 1 行 logger**）
- [ ] 1.3 `attention/engine.py:93` 的 DEBUG 提成 INFO；加 `NOX_LOG_LEVEL`（1h）
      （那行是"他为什么**没有**开口"的唯一证据，被写死的 INFO 吞掉了）
- [ ] 1.4 `/api/health` 加 "last success"：backup / decay / attention / archiver / grow（半天）
      **判据**：这五项停掉任何一项，你能立刻看见。
- [ ] 1.5 `monitor.sh` 对齐 `doctor.sh` 的服务清单（补上 **nox-core**）+ CRIT 推到手机（半天）
      （现状：monitor.sh 不监控 nox-core，告警只写进 `/var/log/nox-monitor.log` —— 而 `log-digest.py` 只读 journalctl，**没人读那个文件**）
      **判据**：`systemctl stop nox-core`，你手机收到推送。
- [ ] 1.6 systemd 加 watchdog；`RestartSec` + `StartLimitBurst`（1h）
      （现状：坏服务 5 秒一次无限重启，含 caddy → 反复重启 = 全网 502）
- [ ] 1.7 `monitor.sh` 里去掉内存压力下的 `drop_caches`（15min）
      （4GB 无 swap 的机器上主动丢 page cache 会推高 OOM 概率）

**这条线的总判据**：随便挑一个"他今天怪怪的"的时刻，你能在 **10 分钟内**从日志回答 —— 这轮 prompt 里有什么、他调了什么、他为什么决定说/不说。**现在做不到，这条线做完就做得到了。**

> trace id 那部分**先不做**。turn 日志已经拿到 80% 的收益。

---

## 第 2 条线：状态正确性（第 4–5 周）

- [ ] 2.1 session id 进 `ToolContext`，删掉 `core.current_session_id` 这个进程级实例属性（1 天）
      （现状：请求线程和后台 Care 线程都会改写它 → 主动消息挂到别的会话上，静默）
      **判据**：手机和桌面同时聊，纸条/订单不会挂错会话。
- [ ] 2.2 `Sessions._cache`（`api/server.py:341`）+ `AttentionRegistry._items`（`registry.py:162`）加锁（半天）
      **判据**：并发压测不再出现 `dictionary changed size during iteration`。
- [ ] 2.3 写路径挂 `invalidate()`：经期 / 待办 / 音乐 / 体重（半天）
      （现状：**全仓生产代码零次 `invalidate()`** → 她记完经期，HealthProvider 还在用 6 小时的旧缓存）
      **判据**：她记完经期，**下一轮** Nox 就知道。
- [ ] 2.4 `CareLedger` 落盘改同步 + `_persist` 失败上报到 `/api/health`（半天）
      （现状：`service.py:850-859` 吞异常 → DB 抖一次，重启就是**重复开口 + 额度静默归零**）
      **判据**：杀掉进程再起，今天的开口额度不会归零重放。

---

## 第 3 条线：闸门与边界（第 6–7 周）

- [ ] 3.1 `ToolSpec` 加 `side_effect: none | read | write | spend | irreversible`，注册时必填（1 天）
- [ ] 3.2 `AgentLoop._execute` 对 `spend` / `irreversible` 统一拦截（没有确认令牌就拒）（半天）
      **判据**：随便写一个没声明的花钱工具，直接抛错，不给模型碰。
- [ ] 3.3 麦当劳迁到 `/api/nox/orders/{id}/confirm`（1 天）
      （`orders/store.py` 已经有 `merchant` 列，成本很低；`tools/mcd.py:39` 的 `ACTION_TOOLS` 定义后全仓没人用过）
      **判据**：`test_tool_never_places_the_order` 对麦当劳也绿。
- [ ] 3.4 `toy_set` / galatea 公开发帖 / 淘宝 同类处理（1 天）
- [ ] 3.5 `hass_set_state` 校验 `DEVICES` + 域白名单（2h）
      **判据**：传 `homeassistant.turn_off` 被拒。
- [ ] 3.6 `check-boundaries.sh` 进 pre-commit + CI，并扩到卫星层（半天）
      （现状：**没有任何自动化入口，而且本机连 bash 都没有**；R5 还 grep 了本仓库不跟踪的 `nox-app/` → 干净 clone 上永远绿）
      **判据**：故意越界一次，CI 会红。

**这条线做完，你以后新增任何"能花钱/不可逆"的工具，CI 会替你拦。**

---

## 第 4 条线：记忆与数据（第 8–11 周）

- [ ] 4.1 OB 统一 `PRAGMA journal_mode=WAL` + `busy_timeout=5000` + 有限重试（半天）
      （现状：OB 全仓零 PRAGMA，而 nox-core 三个库都设了 —— 同一系统两套标准）
- [ ] 4.2 桶文件改 `tmp + os.replace`；写路径收归 `BucketManager`（1 天）
      （现状：`open(path,"w")` 原地截断写 + 6 个进程写者零文件锁 → 崩一次记忆静默消失）
- [ ] 4.3 加 unarchive；修 `trace` 谎报"已激活"（半天）
- [ ] 4.4 pinned 纳入 token 预算；修 `split_breath` 无头时把 dynamic 当 core 的 bug（半天）
- [ ] 4.5 记忆加来源标记（user / model / external / inferred）+ evidence 标注"这是他的推断"（1 天）
      **这条是"不偏离最初人格"的技术基础。**
- [ ] 4.6 decay 挂 systemd timer（不再靠"有人调用工具才跑"）（2h）
- [ ] 4.7 数据保留策略：先做 `usage_log` / `conversations` / `observations` 三个最大头（1 天）
- [ ] 4.8 部署版本化：照 `Ombre-Brain` 已有的 `vps` remote 模式，nox-core / bridge / 各 MCP 全改 git 部署 + 原子发布 + 回滚（1–2 天）
      （现状：逐文件 scp，已造成过一次"传了一个没传另一个"的事故）
      **2026-09-10 又添一个活样本**：线上 unit **没设** `OMBRE_BUCKETS_DIR`，而**仓库里的 unit 设了** —— 你查备份路径时撞见的。**"看仓库判断线上有什么"在这个项目里已经被证伪两次了。**
      **判据**：`git log` 能回答"线上此刻跑的是哪一版"，且发布失败能一条命令回滚。

---

## 明确"不修"的清单（这个能立刻让你轻松一半）

| 不修 | 为什么 |
|---|---|
| 双前端合并（~95% 重复实现） | 纯重构，不影响运行。等自然时机 |
| 循环依赖（`agent↔tools` 等 4 个环） | 除非你要改 `ToolSpec`，否则不动 |
| Relationship（关系演化）从 0 开始 | 那是**没做**，不是**做坏了**。你想做的时候再说 |
| 假绿测试全清 | 只删那两个 `or True`，其余不动 |
| 死重 2GB+（stackchan firmware 1.1GB 等） | 哪天顺手删 |
| Resonance 接开口决策 | **在闸门和日志修好之前，不做反而更安全** |
| 共影从 `/api/chat` 绕过 Care 出口 | 先记着。等 3.2 的拦截机制做好了再一起收 |

---

## 附：如果某周你只有 4 小时

按这个顺序，一次挑一件，别多看：

1. `0.1` 备份引号（30min）→ 这是全表性价比最高的一行
2. `0.4` 里的 **OpenRouter key**（30min）→ 它在 master 上
3. `0.9` bridge fail-open（30min）
4. `1.3` 把 `engine.py:93` 的 DEBUG 提成 INFO（1h）→ 立刻能看见"他为什么不说"
5. `1.4` `/api/health` 加 last-success（1.5h）

这五件做完，你已经把"最坏情况"和"看不见"这两件事解决掉大半了。

---

## 开工前的 5 个坑（写在最前面，别踩）

1. **密钥轮换顺序不能反**：先配新的 → 重启 → 验证 → 最后才 revoke。
2. **HA token 要改三处**：`ha-mcp` / `nox-core` / `bridge`。
3. **VAPID 换密钥会让所有推送订阅失效** —— 留到最后做，做完要在 App 里重新授权通知。
4. **`touch-server:9333` 绝不能绑回环** —— ESP32 是直连公网 IP 上报的，绑了就废。
5. **先轮换密钥，再清 git 历史** —— 清历史撤不回别人已经 clone 的那份。

---

### 补记（2026-09-12 凌晨）：两条关于「凭据的本质」的教训

**① 「泄露清单」的完整性取决于你从哪儿取样本**

09-11 那次历史重写的表达式清单是从**初始提交的 `touch-mcp/Caddyfile`** 建的 ——
那份里有 4 个 48 位 hex、1 个 32 位 hex、1 个 panel 16 位，
**但 `/watch/` 的 24 位不在那份文件里，所以没进清单**。

结果：**我自己写的审计文档 `CAELUM-架构审计-2026-09-10.md:894` 把完整的
`/watch/` URL 抄了进去**，17 个提交、当前 HEAD 里就有，推在 GitHub 上。
（`/agent/` 的 24 位复查过，零命中 —— 纯属运气。）

> **别用「某个文件里有什么」当泄露清单。** 清单必须从**全历史扫描**建，
> 而且扫描的形状要覆盖所有长度/字符集，不能只覆盖你见过的那几种。

**② 我给这条开的药方有一半是错的：轮换没用**

我写的是「改短时效票**并轮换 hex**」。想清楚之后，**轮换那一半没用**：

`co-watching` 的 token **按设计就必须在客户端里** —— Caelum OS 的 Movies 房要调
`https://noxtang.com/watch/<token>/api/...`，不带它调不通。所以它出现在 `.env.local`、
被 Vite 内联进 `dist/assets/index-*.js` —— **不是泄露，是它的工作方式**。
轮换只管用到下一个安装包为止。

**真正要做的只有短时效票**（同 `musicSig` / scribe-token）：客户端不再持永久钥匙，
而是向 bridge 换一张带签名、会过期的票。这样「安装包里有个固定字符串」
就从「永久全权」降级成「过期作废的一小段」。

> 这跟 09-11 那条是同一个道理的两面：**先问「这个凭据的本质是什么」，再决定怎么修。**
> 改历史 / 轮换 / 打码 —— 三个动作各有各的适用面，用错就是白干：
>   · 服务端凭据（API key、HA 令牌）→ **轮换**才是修复
>   · 客户端凭据（编进安装包的 token）→ 轮换无效，要**换机制**（短时效票）
>   · 文档里不该有的值 → **打码**就够，别为它去重建仓库
---

### 第九批（2026-09-12 凌晨）：两个「公网零鉴权」的门 —— 以及一条被我排错的优先级

**🔴 今天最严重的两个发现，都不属于「密钥藏得多深」，而属于「外面碰不碰得到」**

| 门 | 现状 | 谁能碰 |
|---|---|---|
| `music.noxtang.com` | **公开页面里明文印着 eryu 的服务端密钥**（`client/index.html` 里 `auth()` 的兜底值就是 `/root/eryu/server/.secret`）。任何人不带凭据打开页面 → 看源码 → 拿到钥匙 → 调 `/music/*` 拿她的听歌记录 | **任何能上互联网的人** |
| `netease-mcp.noxtang.com` | MCP 的 SSE 端点**零鉴权**（源码里根本没有鉴权层），而这个服务持有她的**网易云 Cookie** | **任何能上互联网的人** |

实测（从她的机器、走公网、不带任何凭据）：

```
GET https://music.noxtang.com/               → 200，页面 HTML 里带着 64 位 hex
GET https://music.noxtang.com/music/recent   → 带那把钥匙 200 / 5982 字节 JSON（她的听歌记录）
GET https://netease-mcp.noxtang.com/sse      → 200   event: endpoint / data: /message
```

**两个都修了：**

- **`netease-mcp`：直接删掉那个 Caddy 站点块。** Nox 连它用的是 `http://127.0.0.1:3456/mcp`
  （回环）—— **没有任何东西需要从外面访问它**。验证：`/sse` 从 200 → **000**
  （那个域名已经没有站点了），服务本身还活着，别的站点没被误伤。
- **`eryu`：删掉页面里的硬编码兜底 + 轮换 `.secret` + 同步消费者。**
  消费者是**两个**不是一个：`/etc/nox/bridge.env` 和 `/root/nox-core/.env`
  （我一开始只找到一个，普查才出来第二个）。
  验证：旧密钥 403 / 新密钥 200 / 公网页面里 64 位 hex **0 处** / 三个服务 active /
  bridge 探针 eryu·netease·nox_core 全 ok / bridge 的 eryu 代理 200（App 放歌不受影响）。

> ⚠️ 她手动翻歌单那个页面现在会要求输一次 token（它本来就有这个界面）。
> 值在 VPS 的 `/root/eryu/server/.secret`。

**🔴 但这一批真正该记的是：我把优先级排错了**

今天大半天花在「git 历史里的密钥」上，而这两个**全世界都能碰到的门**排在后面。
审计其实标了它们（3c），是我把它们排到了后面。排错的原因是我按**密级**排序：

```
错的：  "这个东西多秘密？"  → 私有仓库里的 key 听起来更严重
对的：  "这个东西外面碰得到吗？" → 443 上零鉴权的接口严重得多
```

**以后按这个顺序：**

1. **公网可达 + 零鉴权** —— 先修，不管它"密不密"
2. **服务端凭据轮换**（真在用的 key / token）
3. 私库历史清理、文档打码、`0.0.0.0` 绑定收口 —— **潜在**风险，排队
4. 架构改造（短时效票那类）—— 想清楚再做

顺带：`0.0.0.0` 那批（她原本想先做的）实测**外面全都连不上**（安全组挡着）——
所以它们是"潜在"不是"活的"，正确地排在后面。

**下一批**
- `reading.noxtang.com` 首页公开可读 —— 但它的 `/api/*` 全部 401（这个是好的），首页只是 UI，可接受
- `panel.noxtang.com` 的 x-ui —— 有自己的随机 basepath + 登录
- `0.0.0.0` 剩下 8 个
- 短时效票（co-watching）