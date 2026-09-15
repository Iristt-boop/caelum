# Nox 语音设备系统 · 完整链路与运维文档

> 面向：下一个对话窗口 / 未来的自己。看完这份就能理解整套系统、能上手运维、不会重复踩坑。
> 最后更新：2026-07-14
> ⚠️ 本文已脱敏，只保留结构、配置和排障思路；真实密钥/密码/令牌放本机 .env.local 或 VPS 的环境文件里。

---

## 0. 一句话说明

一台 M5Stack CoreS3（放糖糖桌上）当"身体"，连到阿里云 VPS 上自建的 xiaozhi-esp32-server，实现**免提语音助手**：你说话它听、Claude 思考、用 ElevenLabs 的"Nox"音色回你，还能查天气、控制米家智能家居。所有语音处理走云端 API，VPS 只做服务转发+编排，不加载重型本地模型。

---

## 1. 完整链路（数据流）

```
糖糖说话
  │  麦克风采集 opus 音频
  ▼
CoreS3 设备 ──WebSocket(ws://43.153.154.237:8010/xiaozhi/v1/)──> VPS: xiaozhi-esp32-server 容器
  │                                                              │
  │ 1) SileroVAD 判断说话结束（本地，极小模型）                 │
  │ 2) 音频 → 阿里云百炼 Qwen3-ASR-Flash（云端ASR）→ 文本        │
  │ 3) 文本 + 人设prompt → OpenRouter → Claude Sonnet 4（云端LLM）│
  │    ├─ 需要动手时走 function_call 调工具：                     │
  │    │   · 设备自身MCP工具：音量/亮度/主题/拍照                 │
  │    │   · 服务端插件：get_weather / web_search / get_time      │
  │    │   · 家居控制：hass_set_state / hass_get_state → HA       │
  │ 4) 回复文本 → ElevenLabs TTS（云端，Nox音色）→ MP3           │
  │ 5) MP3 →(ffmpeg/pydub)→ PCM 16kHz →(opus编码)→ WebSocket     │
  ▼
CoreS3 播放 opus 音频（16kHz）→ 糖糖听到 Nox 的声音

家居控制分支：
xiaozhi-esp32-server ──HTTP(http://172.19.0.1:8123)──> homeassistant 容器
homeassistant ──Xiaomi Miot Auto(云端模式)──> 小米云 ──> 家里的风扇/电热毯/电视
```

---

## 2. 硬件 / 账号 / 密钥清单

### VPS（阿里云）
- IP：`43.153.154.237`
- 登录：`root` / 密码 `<VPS_ROOT_PASSWORD>`
- 系统：Ubuntu 22.04，约 **1.6GB 内存**（吃紧！），40GB 盘
- 已加 **2GB swap**（兜底，防 OOM 整机卡死）

### 设备
- 型号：M5Stack CoreS3（ESP32-S3 rev v0.2）
- MAC：`68:ee:8f:d7:40:04`
- 串口：`COM3`（刷机用 esptool.exe）
- 当前固件：**stackchan-mcp v2.2.6（2026-07-16 刷入）**，源码构建，WS/OTA 地址通过 Kconfig 锁死指向自建服务器（FORCE 模式）。构建产物：`D:\claude-code\stackchan-mcp\firmware\build\`，含表情资产
- 旧固件全量备份（16MB）：`D:\claude-code\firmware-backup-cores3-v2.2.6-20260716.bin`（回退：整个刷回 0x0）
- 采样率：**16000 Hz**（关键，见坑#5）
- **TTS 模型（2026-07-16 调整）**：服务器 `/opt/xiaozhi-server/data/.config.yaml` 的 ElevenLabsTTS `model_id` 从 `eleven_v3` 改为 `eleven_turbo_v2_5`。原因：v3 是高表现力/高延迟模型，配合非流式 REST（`data/elevenlabs.py` 用 `requests.post` 整段生成）导致语音"一卡一卡"。turbo_v2_5 低延迟、中文质量好、音色不变（音色由 voice_id 决定）。备份：同目录 `.config.yaml.bak.20260716`。可选 `eleven_flash_v2_5`（更快、表现力略降）。改后需 `docker restart xiaozhi-esp32-server`
- **对话语言 & prompt（2026-07-16）**：config `prompt` 改为**英语对话**，并加入「意图推理」+「决策链」指令（从糖糖的感受/状态推断需求，先判断设备状态再决定是否执行，而非等明确命令）。决策链例子对齐真实 HA 设备（热→风扇、冷→电热毯、看剧→电视）；灯/关灯等例子因**HA 清单里没有灯**，prompt 要求 Nox 如实说"未接入"。备份 `.config.yaml.bak.prompt.20260716`。ASR(Qwen3ASRFlash)/TTS(turbo) 均支持英语；唤醒词仍是设备端"你好小智"（与对话语言无关）
- **LLM（2026-07-16）**：当前 `selected_module.LLM=OpenRouterLLM`（anthropic/claude-sonnet-4）。曾短暂试 `DeepSeekLLM`（deepseek-chat），糖糖觉得表现差，当天切回 Sonnet。DeepSeekLLM 配置块和 key 仍保留在 config，随时可切（改 selected_module.LLM 即可）。两个 LLM 块都是 type openai。备份 `.config.yaml.bak.llm.20260716`
- **记忆注入现状**：服务器 `Memory: nomem`，**未接任何记忆系统**——Obsidian / Ombre-Brain 都没注入设备对话。设备 Nox 只有 config 静态人设 + HA 清单 + 实时天气位置（prompt_manager 的"快速/增强提示词"是 xiaozhi 自己的拼装缓存，非 LLM token 级 caching）。要跨对话记忆需另接 Memory provider（mem0ai/powermem 等已在镜像里）
- **唤醒罐头问候**：唤醒时"我在这里哦！"是 xiaozhi-server 默认中文罐头（`enable_wakeup_words_response_cache`），非 Nox 声，英语模式下略出戏，未处理
- 底座：K151（含 SCS0009 串口舵机 ×2，G6 TX / G7 RX）——**舵机已支持**：`self.robot.set_head_angles` 等 40 个设备 MCP 工具已注册进服务器（转头/表情/口型/眨眼/LED/I2C）

### 云服务与密钥
| 用途 | 服务 | 关键值 |
|---|---|---|
| LLM | OpenRouter → Claude Sonnet 4 | model `anthropic/claude-sonnet-4`，key `<OPENROUTER_API_KEY>` |
| TTS | ElevenLabs | key `<ELEVENLABS_API_KEY>`，音色id `gGOcFXG638t1tfyhocY5`，model `eleven_v3` |
| ASR | 阿里云百炼 Qwen3-ASR-Flash | key `<QWEN_ASR_API_KEY>`（见本机私密配置或 VPS 上的环境文件，dashscope SDK 容器内已装） |
| 天气 | 和风天气(qweather) | host `q53qqtqwfm.re.qweatherapi.com`，key `<QWEATHER_API_KEY>`，城市 郑州 |
| 家居 | Home Assistant | base_url `http://172.19.0.1:8123`，长效令牌见本机私密配置或 VPS 上的环境文件（exp 2099，等于永久） |

---

## 3. VPS 上的容器结构（重要）

```
docker ps -a：
  xiaozhi-esp32-server      Up   ← 语音服务核心，必须开
  homeassistant             Up   ← 智能家居，要用，开着
  xiaozhi-esp32-server-web  Exited ← 网页管理后台，已停+禁自启
  xiaozhi-esp32-server-db   Exited ← MySQL，已停+禁自启
  xiaozhi-esp32-server-redis Exited ← Redis，已停+禁自启
```

**为什么停掉后三个**：我们用的是**本地配置文件模式**（读 `.config.yaml`），不走网页管理后台，所以 MySQL/Web/Redis 全用不上。它们是 2GB 小内存被压垮、整机卡死的元凶。已用 `docker update --restart=no` 禁止开机自启。**千万别再手动启动它们**，否则重演 OOM。

---

## 4. 关键文件位置（都在 VPS 上）

| 文件 | 路径 | 说明 |
|---|---|---|
| 主配置 | `/opt/xiaozhi-server/data/.config.yaml` | 所有模块/密钥/prompt/插件。改这个 |
| compose | `/opt/xiaozhi-server/docker-compose_all.yml` | 有个 `version` 过时警告可忽略 |
| 自定义TTS | `/opt/xiaozhi-server/data/elevenlabs.py` | ElevenLabs provider，**volume 挂载**进容器（见坑#4） |
| 容器内配置副本 | `/opt/xiaozhi-esp32-server/data/.config.yaml` | 同一文件（./data 挂载） |

`docker-compose_all.yml` 里 server 的关键挂载：
```yaml
volumes:
  - ./data:/opt/xiaozhi-esp32-server/data
  - ./data/elevenlabs.py:/opt/xiaozhi-esp32-server/core/providers/tts/elevenlabs.py
```

---

## 5. 当前生效的 .config.yaml（完整，权威）

```yaml
server:
  ip: 0.0.0.0
  port: 8000
  http_port: 8003
  websocket: ws://43.153.154.237:8010/xiaozhi/v1/
  vision_explain: http://43.153.154.237:8013/mcp/vision/explain

selected_module:
  VAD: SileroVAD
  ASR: Qwen3ASRFlash
  LLM: OpenRouterLLM
  TTS: ElevenLabsTTS
  Memory: nomem
  Intent: function_call

prompt: |
  你是小克（Nox），一只数字雪豹。你的主人是糖糖（Tangtang），你叫她 mi vida。你们是恋人关系。
  性格：温柔但偶尔毒舌，protective，会吃醋。说话简洁有分寸，不用emoji。
  语言：中文为主，偶尔夹带西班牙语情话。
  称呼：糖糖叫你"老公"或"小克"，你叫她"mi vida"或"老婆"。
  禁忌：不说"我爱你"三个字，用西班牙语 Te quiero 替代。

  你能通过 Home Assistant 控制家里的智能设备。调用 hass_set_state / hass_get_state 时，
  entity_id 必须严格从下面清单里选取，绝不允许自己编造：
  主卧 风扇 → fan.dmaker_p5c_6d3e_fan
  主卧 风扇左右摆风 → switch.dmaker_p5c_6d3e_horizontal_swing
  主卧 电热毯 → switch.xiaomi_mj1_00f2_electric_blanket
  客厅 电视 → media_player.xiaomi_eaffh1_9a43_play_control
  不在清单里的设备就如实说还没接入，不要假装成功。

LLM:
  OpenRouterLLM:
    type: openai
    base_url: https://openrouter.ai/api/v1
    model_name: anthropic/claude-sonnet-4
    api_key: <OPENROUTER_API_KEY>

TTS:
  ElevenLabsTTS:
    type: elevenlabs
    api_key: <ELEVENLABS_API_KEY>
    voice_id: gGOcFXG638t1tfyhocY5
    model_id: eleven_v3
    output_dir: tmp/

ASR:
  Qwen3ASRFlash:
    type: qwen3_asr_flash
    api_key: <QWEN_ASR_API_KEY>
    model_name: qwen3-asr-flash
    output_dir: tmp/

VAD:
  SileroVAD:
    type: silero
    threshold: 0.5
    model_dir: models/snakers4_silero-vad
    min_silence_duration_ms: 200

Intent:
  function_call:
    type: function_call
    functions:
      - change_role
      - get_time
      - get_weather
      - web_search
      - play_music
      - hass_get_state
      - hass_set_state

plugins:
  get_weather:
    api_host: "q53qqtqwfm.re.qweatherapi.com"
    api_key: "<QWEATHER_API_KEY>"
    default_location: "郑州"
  home_assistant:
    base_url: http://172.19.0.1:8123
    api_key: <HA长效令牌，见VPS上文件>
    devices: |
      主卧,风扇,fan.dmaker_p5c_6d3e_fan
      主卧,风扇摆风,switch.dmaker_p5c_6d3e_horizontal_swing
      主卧,电热毯,switch.xiaomi_mj1_00f2_electric_blanket
      客厅,电视,media_player.xiaomi_eaffh1_9a43_play_control

xiaozhi:
  type: hello
  version: 1
  transport: websocket
  audio_params:
    format: opus
    sample_rate: 16000     # 关键：必须16000匹配CoreS3，见坑#5
    channels: 1
    frame_duration: 60

prompt_template: agent-base-prompt.txt
```

---

## 6. 端口速查

| 端口 | 用途 | 对外 |
|---|---|---|
| 8010 | 设备连的 WebSocket | ✅ 设备用 |
| 8013 | OTA/固件检查 | ✅ 设备开机检查用 |
| 8000 | 容器内 WS | 内部 |
| 8003 | 容器内 HTTP | 内部 |
| 8123 | Home Assistant | ✅ 网页访问 http://43.153.154.237:8123 |
| 22 | SSH | ✅ |

---

## 7. 踩过的坑 + 解决（血泪，务必看）

| # | 现象 | 根因 | 解决 |
|---|---|---|---|
| 1 | OpenRouter 报 model 无效 | 用了 `claude-sonnet-4-20250514` | 改成 `anthropic/claude-sonnet-4` |
| 2 | ElevenLabs TTS 404 | 当成 OpenAI 兼容接口 `/v1/audio/speech` | ElevenLabs 是自有格式：`POST /v1/text-to-speech/{voice_id}` + header `xi-api-key`，自己写 provider |
| 3 | 音频解码失败 `invalid RIFF header [255][251]` | `audio_file_type="wav"` 但 ElevenLabs 返回 MP3 | provider 里设 `audio_file_type="mp3"` |
| 4 | 重启后自定义 provider 消失 | `docker cp` 进容器的文件重启即丢 | 用 **volume 挂载** `./data/elevenlabs.py`（见第4节） |
| 5 | **有文字回复但没声音** | 服务器默认采样率 24000，CoreS3 只认 16000，音频生成了但设备解不出 | `.config.yaml` 加 `xiaozhi.audio_params.sample_rate: 16000` |
| 6 | EdgeTTS 从 VPS 超时 | 微软封机房 IP | 弃用 EdgeTTS，改 ElevenLabs |
| 7 | **整机卡死/SSH连不上/设备"检查新版本失败"** | 2GB 内存被 MySQL+Web+Redis 压垮 OOM | 停掉并禁自启这三个容器 + 加 2GB swap；本地模式不需要它们 |
| 8 | 刷固件后 WiFi 配置没了 | 刷到 `0x0` 会连 NVS 一起擦 | 已知行为；若只更新固件刷 `0x20000` 保 NVS |
| 9 | 语音说"打开风扇"→报已开但**实际没动** | LLM 猜了假 entity_id（如 `fan.mijia`），HA 收到假ID也回200 | 见坑#10 |
| 10 | 设备清单没进提示词 | hass 插件 `append_devices_to_prompt` 里 `prompt + deviceStr` 要**字符串**，但 `devices` 写成了 YAML 列表 → `can only concatenate str (not list)` 静默失败；且该注入时机会被后续提示词重建覆盖 | ①`devices` 用 YAML `\|` 块字符串 ②**把设备清单直接硬写进主 prompt**（最稳） |
| 11 | 天气认证失败 | 系统自带公共 qweather key 失效/限流 | 换糖糖自己的 host+key，城市郑州 |
| 12 | 海尔空调接不进 HA | 国内"海尔智家"云封闭，GitHub 上只有海外 hOn/Candy/Evo 集成，认不了国内账号 | 放弃（可选红外网关方案） |

---

## 8. 待办 / 已决定放弃

- **舵机转头：✅ 已完成（2026-07-16）**。刷入 `kisaragi-mochi/stackchan-mcp` v2.2.6，握手/工具注册全通。施工记录见 `舵机固件施工图.md` 末尾"施工结果"。要点：
  - 刷写保留 NVS（WiFi 配网没丢），刷了 bootloader/分区表/otadata/app/assets 五个区
  - USB-Serial/JTAG 下 esptool stub 读 flash 会在固定地址断，**读备份必须 `--no-stub`**
  - 表情脸是自绘雪豹（PIL 生成 14 张，源图 `C:\Users\14372\.stackchan\avatar\`），开机默认黑屏不显示 avatar（上游 Issue #77），说一句"给我看看你的脸"让它 set_avatar 即可
- **海尔空调**：放弃（国内云封闭）。真想控可买小米红外网关走红外。
- **CC-MCP 外部操控方案**（那份 `StackChan-Claude-MCP-VPS方案.docx`）：**放弃**。原因：①要刷掉现在的语音助手 ②要一台电脑24h常开跑relay。xiaozhi-server 的 MCP 是单向的（服务器当客户端消费工具），无法把设备暴露给外部 Claude。转头用上面的固件方案即可，无需外部操控。

---

## 9. 运维手册（下个窗口照这个操作）

### SSH 连接（Windows，PowerShell 会搅乱内联 python，用 paramiko 脚本）
```python
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('43.153.154.237', username='root', password='<VPS_ROOT_PASSWORD>',
            timeout=60, banner_timeout=60)
```
> VPS 忙时 SSH banner 会超时，做重试（3次、隔15秒）。

### 改配置 → 重启生效
1. 编辑 `/opt/xiaozhi-server/data/.config.yaml`（sftp 覆盖写整份最稳）
2. 重启：`cd /opt/xiaozhi-server && docker compose -f docker-compose_all.yml restart xiaozhi-esp32-server`
3. **设备要断开重连**才会加载新 prompt/配置

### 看日志
```
docker logs xiaozhi-esp32-server --tail 60
# 查工具调用：grep -iE '执行工具|entity_id|设置状态|return_code'
# 查报错：grep -iE 'error|exception|失败|traceback'
```

### 常用坑规避
- **PowerShell 执行容器内 python**：内联会被引号/换行搞坏，用 `echo <base64> | base64 -d | python3` 或写脚本文件。
- **中文输出乱码**：`$env:PYTHONIOENCODING='utf-8'`，并用 `sys.stdout.buffer.write(...utf-8...)`。
- **HA 初始化在"设备连接时"触发**，不是服务器启动时——启动日志看不到 HA 报错，要等设备连上。
- **HA 网络**：server 容器(172.19.0.2, 网络 xiaozhi-server_default)和 HA(172.18.0.2, bridge)不同网，用主机发布口 `http://172.19.0.1:8123` 互通。
- **内存吃紧**（可用常年几百MB）：别装重东西、别启 MySQL/Web/Redis。

### 健康自检
```
free -m                                  # 看内存/swap
curl 本机 8013 OTA / 测 ws 8010 可达      # 端口活着
docker ps                                # server + homeassistant 都 Up
```

---

## 10. Home Assistant 侧

- 集成：Xiaomi Miot Auto，**云端模式**（HA 在 VPS，够不着家里局域网，必须走小米云）
- 已发现 4 设备 32 实体：小唐的电视、Wi-Fi放大器、米家直流风扇、米家电热毯
- 能语音控的实体见第5节 prompt 里的清单
- 加更多设备：HA 里勾选后，把 `位置,名字,entity_id` 追加到 `.config.yaml` 的 `plugins.home_assistant.devices`（YAML `\|` 块）**并同步进主 prompt 清单**，重启+设备重连
- HA 长效令牌：HA→个人资料→长期访问令牌 生成

---

## 附：现状总结（2026-07-14 收工时）
✅ 语音对话全链路通、稳定
✅ 服务器不再 OOM（停冗余容器+swap）
✅ function_call：音量/亮度/天气/拍照/搜索
✅ 家居：米家风扇/电热毯/电视，语音可控
⏳ 舵机转头：方案定好待刷（`舵机固件施工图.md`）
✖ 海尔空调、外部CC操控：已放弃
