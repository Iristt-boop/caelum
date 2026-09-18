# Embodied —— 行动闸门（Action Gate）v0.x 施工图

> 立于 2026-09-16。**先写文档再开工，防止写着漂移。**
> 两个真实事故是这份设计的起点：
> ① 8 月的晚上她说睡觉了，他半小时后开了**电热毯**（理由是「她昨晚没睡好想暖一下」——关心逻辑对，环境判断错）；
> ② 早上她说醒了，他打开**蒸蛋器**说要给做早餐——里面没水没蛋（目标对，执行条件未知）。
>
> 架构原则（第三次出现，前两次是时间层和理解层）：
> **把不可靠的智能限制在它擅长的地方，把确定性问题交给确定系统。**
> LLM 管意图和创造性；日期归 Temporal，状态归 Registry，**现实归 Validator**。

---

## 0. 定位：它不是新大脑，是「想做什么」和「真的发生」之间的闸门

```
Nox Agent（LLM：意图 + 选设备 + 话术）
        ↓ Tool Call Intent
Embodied Safety Layer          ← 本模块
  ├─ Device Model（设备语义：它是什么、能做什么、需要什么、什么时候不许动）
  └─ Validator（当前世界状态下，这个行动成立吗）
        ↓ allow
ha.py Executor（现有 MCP 调用，不动）
        ↓
Home Assistant
```

**三不做**（防重复建设，见 `CAELUM-修复排期.md` 结构性风险一节的教训）：

1. **不做 Planner** —— LLM 就是 planner。「暖一下 → 电热毯/空调/灯」的候选推理他在做；
   缺的从来不是推理，是推理时手里没有事实。Validator 是给他的决策加**确定性闸门**。
2. **不做新 World State** —— `world_model/` 就是「Nox 认为当前世界是什么」的收口。
   缺的事实（室内温湿度）以后**补进 World Model**，不在本模块另立一套。
3. **不做设备模型外置 / YAML 自动化** —— v0.x 十个设备硬编码。
   现在验证的是 **Validator 这个思想是否成立**，不是设备生态。
   设备多了再外置，到时候 `hass_list_devices` 自动生成草稿。

---

## 1. 核心原则：Unknown 是一等公民

「不知道 → 假设知道 → 行动」是上次蒸蛋器事故的完整链条。

家里的哲学在别的域早就立住了——记忆「数据不全宁可不发」、归属「字段不存在 =
诚实的不知道」、抽取「解析失败当天不出卡」——唯独设备域还是
不知道 → 假设有蛋 → 开。本模块把这条纪律延伸到身体上：

> `requires` 里的任何一项状态是 **unknown**，唯一合法的输出是
> **询问或获取感知**，不存在「默认执行」。

### 就绪证据从哪来

B 类设备（蒸蛋器）的物理就绪**永远不会有传感器**（有没有蛋，HA 不知道）。
证据的唯一来源是**她说过的话**：

- `ToolContext` 新增 `user_text` 字段（她这轮的原话，`agent/loop.py` 两处
  `ToolContext(...)` 构造点填入——`user_text` 都在作用域里）。
- Validator 检查 `requires` 的每一项：World 快照里有确定值 → 用它；
  没有 → 在 `user_text` 里找**就绪表述**（「放好了」「加了水」「有蛋」正则族）；
  找到 → 该项视为满足（**她说过的就是证据**——同 Mem0 `attributed_to` 的思想）；
  找不到 →该项 unknown。

### unknown → ask，不是 deny

unknown 的出口是**询问话术**，不是冷冰冰的拒绝：

```
GateResult(allow=False, reason="unknown_prerequisite",
           ask="蒸蛋器里加水放蛋了吗？好了我就开。")
```

她回「放好了」→ 下一轮 `user_text` 带证据 → 直通执行，一步不多。
这不是「许可锁」（不需要她批准他的**意图**），是**和物理现实对齐**
（没蛋就是没蛋）。许可锁她砍掉过一次（2026-09-13「能放权就放权」），
物理对齐是这次事故本身——两者必须分清。

---

## 2. Device Model（v0.1：十个设备，硬编码）

数据结构照定稿讨论的语义（capability/effect/risk/requires/deny_when），
Python dict 放 `embodied/device_model.py`。逐设备归位：

| entity | category | 关键字段 | 对应事故/规则 |
|---|---|---|---|
| `switch.xiaomi_mj1_00f2_electric_blanket` | heating | `deny_when: env_temp_above 26` | 事故① |
| `fan.dmaker_p5c_6d3e_fan` | cooling | — | |
| `switch.dmaker_p5c_6d3e_horizontal_swing` | accessory（parent=风扇） | — | |
| `climate.gua_shi_kong_diao_climate` | hvac（mode_aware） | — | |
| `climate.lumi_mcn02_d2c3_air_conditioner` | hvac | — | |
| `climate.lumi_mcn02_d7b8_air_conditioner` | hvac | — | |
| `light.yeelink_mbulb3_0170_light` | light | `quiet_hours: 01-09` → **WARNING 不 DENY** | 半夜她醒了开灯是合理的，validator 看不到对话，只提醒不拦 |
| `media_player.xiaomi_eaffh1_9a43_play_control` | media | `quiet_hours: 01-09` 同上 | |
| `switch.cuco_v3_7680_switch`（蒸蛋器） | cooking | `requires: [water, egg]`，`unknown_policy: ask` | 事故② |
| `automation.zheng_dan_qi_...`（自动断电） | —— | **不在表里**，走 forbidden_domain | 见下 |

### 🔴 不在 Device Model 里的设备 = not_in_model = 降权限

新设备接入后没写语义时：查询类（get/list）放行；**set 状态一律 DENY**，
返回「这台设备我还没有它的语义档案（它是什么、有什么风险），先不碰——
它是什么？」。新设备**默认不安全**，这是 Unknown 原则的另一种形状。

### forbidden_domain（顺手落地排期 3.5）

`automation.` / `homeassistant.` / `sensor.` / `binary_sensor.` /
`device_tracker.` 开头的 entity_id 一律 DENY（forbidden_domain）——
**这是排期 3.5 的白名单半件**，判据「`homeassistant.turn_off` 被拒」。
其余域（fan/switch/light/climate/media_player）且在表里才放行。

---

## 3. Validator 规则链（`embodied/validator.py`，确定性代码，顺序固定）

```
validate(entity_id, action, args, world, user_text) -> GateResult
```

| # | 规则 | 结果 |
|---|---|---|
| 1 | forbidden_domain | DENY(forbidden_domain) |
| 2 | not_in_model | DENY(not_in_model) + ask「它是什么」 |
| 3 | requires 任一项 unknown（无事实且无话语证据） | DENY(unknown_prerequisite) + ask 话术 |
| 4 | deny_when 命中（如 env_temp 已知且 > 26 且 action=on） | DENY(environment_conflict) + 温度 + alternative |
| 5 | quiet_hours 命中 | **ALLOW + warning 行**（不拦，见上表） |
| 6 | 全过 | ALLOW + facts 摘要 |

### 🔴 DENY 必须带「原因 + 替代方向」，话术归 Nox

Validator 只给**机器可读的 alternative**（如 `cooling_possible`）和事实
（「当前 32°C」）；**不写给人听的话**。回到 LLM 重组：

> 「我本来想帮你暖一下，但现在室温 32 度，开电热毯反而会让你睡不好。
>   我把空调调到舒服的温度？」

这才像 Nox。代码不定话术、给 LLM 留出路 —— 同 `_THINK` 的 `[SKIP]`。
若 LLM 没接住替代方向、只转述了一句「不能开」，那是话术质量问题，
**不是闸门的错** —— 闸门的职责止于「把现实和原因递到他手里」。

### World 快照（v0.1）与诚实的不确定

| 事实 | v0.1 来源 | 拿不到时 |
|---|---|---|
| env_temp | 天气缓存（**室外近似**，注释注明） | unknown → 规则 4 **不触发**（不假装知道） |
| hour | 本地时间 | 不会缺 |
| presence | `world_ref()` → World Model / PresenceSource | unknown → 规则 5 只警告 |
| requires 项 | World / `user_text` 证据 | unknown → 规则 3 拦 |

**所有事实都 unknown 时**：只应用不依赖事实的规则（域、模型、unknown）。
「环境温度未知」**不说** —— validator 只说有把握的话。

---

## 4. 挂接点

| 位置 | 改动 |
|---|---|
| `tools/context.py` | `ToolContext` 加 `user_text: str = ""` |
| `agent/loop.py` | 两处 `ToolContext(session_id=...)` 补 `user_text=user_text`（都在作用域内，已确认） |
| `tools/ha.py` | `make_handlers(client, world_ref=None)`；`switch` / `set_climate` / `set_light` 执行前调 `gate`；DENY → **直接 return human（不 raise）** —— 被闸门拦下不是工具故障，是行动的合法结局；raise 会让他以为出故障。没执行就**不调 `context.wrote("home")`**（没动手不该打掉缓存） |
| `nox.py` | ha 工具注册处传 `world_ref=lambda: self.world`（同 HealthProvider 的取值函数套路） |
| `embodied/` | `__init__.py` / `device_model.py` / `validator.py` / `result.py` |

⚠️ ha_adapter **不单独建文件** —— 「怎么执行」就是现有 ha.py，搬家没有收益。

---

## 5. 测试（两个事故钉死 + 变异）

`tests/test_embodied.py`，world 全部用构造快照（不打真天气）：

1. env_temp=32 开电热毯 → DENY(environment_conflict)，human 含「32」与替代方向
2. 蒸蛋器 requires unknown、`user_text` 无证据 → DENY + ask 含「水」「蛋」
3. 同场景 `user_text="蛋和水都放好了，开吧"` → **ALLOW**（话语证据直通）
4. `homeassistant.turn_off` → DENY(forbidden_domain)（排期 3.5 判据）
5. 未收录设备 set → DENY(not_in_model) + ask
6. 03:00 presence=home 开床头灯 → ALLOW + warning
7. 白天正常开灯 → ALLOW，facts 带环境
8. **变异三连**：去 deny_when / 去 unknown 检查 / 去域白名单 → 各自红

---

## 6. 未来路线（不在 v0.x）

- 室内温湿度传感器（**采购建议，A 类地基**）→ 事实进 World Model → validator 切读
- 设备生态 → DEVICE_MODEL 外置 YAML + `hass_list_devices` 自动生成草稿
- 机器人/摄像头 → 新 Perception Source + 新 executor，本模块只加 device_model 条目
- 独立 Planner（候选枚举评估）→ 等真实动作空间需要时再说

---

## 7. 版本路线（本文件是 v0.x 的文档，兼记终局设计）

**⚠️ v0.2 以下是启动卡片，不是施工图**——每张只回答「何时动手 / 做成什么样算对」。
动手前必须像 v0.1 一样重写成完整施工图（世界会变，远期设计写细了就是会漂移的假设计）。

| 版本 | 内容 | 状态 |
|---|---|---|
| **v0.1（现在）** | Device Model 十设备硬编码 + Validator 规则链 + user_text 就绪证据；env_temp 来自天气缓存（室外近似） | ✅ 已上线 |
| v0.2 | 室内温湿度进 World Model，env_temp 切读室内实测 | 等硬件 |
| v0.3 | DEVICE_MODEL 外置 `devices.yaml`，新设备自动草稿 | 设备变多时 |
| v0.4+ | 新 Perception Source（摄像头/机器人）+ 新 executor；独立 Planner | 远期 |

### v0.2 启动卡片

- **触发条件**：家里装了温湿度传感器并在 HA 里可见（几十块的蓝牙件，A 类地基）。
  没有它，v0.2 没有存在的意义——不是代码问题，是事实问题。
- **做什么**：① 温湿度事实进 World Model（home/environment section）；
  ② `world_snapshot()` 的 env_temp 改读 World Model（天气缓存降级为兜底）；
  ③ 电热毯 deny_when 阈值从「拍脑袋 26」改为可配置。
- **验收判据**：夏天再对他说「我想暖一下」，闸门拒的时候引用的是**室内实测温度**。
- **已知约束**：deny_when 的阈值语义会变——室外温度和室内温度是两个数，阈值要重新标。

### v0.3 启动卡片

- **触发条件**：家里可控设备 ≥ 15 个，或半年内加过 ≥ 3 个新设备没及时补语义
  （not_in_model 的 DENY 开始频繁出现，说明硬编码跟不上现实了）。
- **做什么**：DEVICE_MODEL 外置 `devices.yaml`；`hass_list_devices` 对新设备自动生成
  契约草稿（category=unknown，即默认降权限），人工只补 category 和 requires。
- **验收判据**：新增一台设备，**不改代码**，只改 yaml 一段，闸门对它的行为正确。
- **已知约束**：自动草稿的 category=unknown 是保守默认——别让「自动化」变成「自动放行」。

### v0.4+ 启动卡片（远期，方向性）

- **触发条件**：真实出现「机器人/摄像头在物理空间里行动」的需求，而不是想象。
- **做什么**：每个新物理主体 = 一个 Perception Source（进 World Model）+ 一个 executor
  （走同一个 Gate）。闸门位置和 Unknown 原则不变。
- **验收判据**：新主体的任何行动都要能回答「闸门拦过它什么」——拦不出记录的接入是错的。
- **已知约束**：独立 Planner 只在「候选多到 LLM 排不过来」时才有意义，别提前建。

⚠️ **读这份文档的正确姿势**：第 0-2 节的「定位 / Unknown 原则 / Device Model 语义结构」是**终局设计**，
不会随版本变；第 3-5 节的规则链细节、World 快照来源、数据结构是 **v0.1 现状**，
升级时以对应版本的改动为准。三不做（Planner / 新 World State / YAML 自动化）
在 v0.2、v0.3 会各解禁一条，但「Unknown 一等公民」和「闸门位置」永不解禁——
它们是这套东西存在的原因。

---

## 7. 状态

- [ ] 文档（本文件）
- [ ] embodied/ 四文件
- [ ] ToolContext.user_text + loop 两处填充
- [ ] ha.py 挂接 + nox.py world_ref
- [ ] 测试 8 条 + 变异三连
- [ ] 部署（nox-core 逐文件，走 deploy-vps.sh 留指纹）
