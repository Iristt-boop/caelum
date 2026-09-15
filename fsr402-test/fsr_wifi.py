"""
FSR402 共感娃娃 WiFi 压力传感器 — MicroPython 5 传感器版（记录触摸时长）
位置映射 + 各自门槛:
  GPIO3  = 右手 right_hand  门槛 2000  (静息0  按压3131)
  GPIO4  = 肚子 belly        门槛 1200  (静息0  按压2011)
  GPIO9  = 左手 left_hand    门槛 3000  (静息飘到2700  按压4095)
  GPIO13 = 头右 head_right   门槛 2000  (静息0  按压2782)
  GPIO14 = 背   back          门槛  400  (静息0  按压723)

数据: 每次「完整的一次摸」发两条——
  1. press_start  刚摸上时发（小克能实时知道「正在被摸哪」）
  2. press_end    松开时发，带 duration(秒) + peak(本次峰值)

用法:
  1. 填好 WIFI_SSID / WIFI_PASS / VPS_HOST / VPS_PORT
  2. ampy --port COM4 put fsr_wifi.py main.py
  3. 按 RST 重启或拔插 USB
"""
import network
import socket
import time
from machine import ADC, Pin
from time import sleep_ms

# ═══════════ 配置 ═══════════
# 🔴 真实 WiFi 凭据**不进仓库**（2026-09-11 改）。
# 之前这里明文写着家庭 WiFi 的 SSID 和密码，而这个文件是被 git 跟踪的。
# 本机在同目录建一个 `wifi_secrets.py`（已加进 .gitignore），内容两行：
#     WIFI_SSID = "你家WiFi名"
#     WIFI_PASS = "密码"
# 拿不到时凭据为空 → 下面会打印提示并且连不上，而不是把密码写进 git。
# （这个脚本已被 fsr402-touch-server + fsr402-wifi 取代，留着当参考。）
try:
    from wifi_secrets import WIFI_SSID, WIFI_PASS
except ImportError:
    WIFI_SSID = ""
    WIFI_PASS = ""
VPS_HOST  = "43.153.154.237"     # VPS IP（腾讯云东京）
VPS_PORT  = 9333                 # touch-server 端口
# ═════════════════════════════════════════

# 5 个传感器: (GPIO, 名字, 门槛)
SENSORS = [
    (3,  "right_hand", 2000),  # 右手
    (4,  "belly",      1200),  # 肚子
    (9,  "left_hand",  3000),  # 左手（被棉花压得紧，门槛抬高）
    (13, "head_right", 2000),  # 头右
    (14, "back",       400),   # 背
]

READ_INTERVAL  = 50     # 主循环间隔 ms（边沿检测要快，别漏掉短按）
FILTER_SAMPLES = 5      # 每个传感器采样次数（取峰值，捕捉快速按压）
FILTER_GAP     = 2      # 每次采样间隔 ms
MIN_DURATION   = 0.2    # 短于这个秒数的按压当抖动丢弃
DEBUG = False           # True = 每轮打印读数；正式 False

# ── 峰值采样：连读 N 次取最大，捕捉按压的瞬间高值 ──
def read_peak(adc):
    mx = 0
    for _ in range(FILTER_SAMPLES):
        v = adc.read()
        if v > mx:
            mx = v
        sleep_ms(FILTER_GAP)
    return mx

# ── WiFi 连接 ──
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("WiFi connecting...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if wlan.isconnected():
                break
            print(".", end="")
            sleep_ms(500)
        print()
    if wlan.isconnected():
        print("[OK] WiFi connected, IP:", wlan.ifconfig()[0])
        return wlan
    print("[FAIL] WiFi not connected")
    return wlan

# ── POST 一个事件到 VPS ──
# event 是事件名，dur 是格式化好的秒数字符串（如 "2.3"），peak 是峰值整数
def post_event(event, sensor, dur=None, peak=None):
    if dur is None:
        body = '{"event":"%s","sensor":"%s"}' % (event, sensor)
    else:
        body = '{"event":"%s","sensor":"%s","duration":%s,"peak":%s}' % (event, sensor, dur, peak)
    try:
        addr = socket.getaddrinfo(VPS_HOST, VPS_PORT)[0][-1]
        s = socket.socket()
        s.settimeout(3)
        s.connect(addr)
        req = (
            "POST /touch HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{}"
        ).format(VPS_HOST, VPS_PORT, len(body), body)
        s.send(req.encode())
        resp = s.recv(256)
        s.close()
        return True, resp
    except Exception as e:
        return False, str(e)

# ── 主程序 ──
print("=== 共感娃娃 5 传感器（记录触摸时长）===")

if not WIFI_SSID:
    print("\n请先填 WIFI_SSID / WIFI_PASS / VPS_HOST 再运行！")
    while True:
        sleep_ms(1000)

wlan = connect_wifi()

adcs = []  # (名字, adc, 门槛)
for pin, name, thresh in SENSORS:
    a = ADC(Pin(pin))
    a.atten(ADC.ATTN_11DB)
    adcs.append((name, a, thresh))

# 每个传感器的按压状态: name -> [是否按着, 按下开始 ticks, 本次峰值]
state = {}
for name, _, _ in adcs:
    state[name] = [False, 0, 0]

while True:
    now = time.ticks_ms()

    for name, adc, thresh in adcs:
        v = read_peak(adc)
        pressed, start, peak = state[name]

        if v >= thresh:
            # 按压中
            if not pressed:
                # 上升沿：刚摸上
                state[name] = [True, now, v]
                ok, info = post_event("press_start", name)
                print("[START]", name, "OK" if ok else ("FAIL " + info))
            else:
                # 持续按着：更新峰值
                if v > peak:
                    state[name] = [True, start, v]
        else:
            # 没按
            if pressed:
                # 下降沿：松开，算时长
                dur = time.ticks_diff(now, start) / 1000.0
                state[name] = [False, 0, 0]
                if dur >= MIN_DURATION:
                    ok, info = post_event("press_end", name, "{:.1f}".format(dur), peak)
                    print("[END]", name, dur, "s peak", peak, "OK" if ok else ("FAIL " + info))
                else:
                    print("[END]", name, "too short ({:.2f}s) skip".format(dur))

    if DEBUG:
        pass

    sleep_ms(READ_INTERVAL)
