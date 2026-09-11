/*
 * FSR402 WiFi 压力传感器 — ESP32-S3
 * 每 200ms 读一次 ADC，压力值变化超过阈值时 POST 到 VPS。
 *
 * 板子：ESP32-S3 N16R8
 * 引脚：GPIO14 (ADC2_CH3)
 *
 * ⚠ 使用前填写下面的 WiFi 和 VPS 信息
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ───────────── 配置 ─────────────
// 🔴 真实 WiFi 凭据**不进仓库**（2026-09-11 改）。
// 之前这里明文写着家庭 WiFi 的 SSID 和密码，而这个文件是被 git 跟踪的。
// 本机在同目录建一个 `wifi_secrets.h`（已加进 .gitignore），内容三行：
//     #define WIFI_SSID "你家WiFi名"
//     #define WIFI_PASS "密码"
//     #define VPS_URL "http://43.133.211.140:9333/touch/<TOUCH_TOKEN>"
// 没有它时凭据为空 → 串口会提示连不上，而不是把密码写进 git。
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
#define WIFI_SSID ""
#define WIFI_PASS ""
#endif

// VPS_URL 允许被 wifi_secrets.h 覆盖 —— 因为 2026-09-11 起 touch-server
// 启用了 TOUCH_TOKEN，**POST 必须打到 /touch/<TOKEN>**（无 token 会被 403）。
// ⚠️ 换句话说：**不更新 wifi_secrets.h 就刷固件，设备会 403，数据传不上来。**
// 改成一个宏而不是 const char*，是为了不改下面任何一行用法（都是字符串字面量）。
#ifndef VPS_URL
#define VPS_URL "http://43.133.211.140:9333/touch"
#endif
// ─────────────────────────────────────────────

const int FSR_PIN       = 14;    // GPIO14 — ADC2_CH3
const int READ_INTERVAL = 200;   // 读数间隔 ms
const int POST_THRESH   = 50;    // 变化超过此值才发送
const int POST_COOLDOWN = 500;   // 最短发送间隔 ms（防止抖动刷屏）

int  lastValue  = -1;            // 上一次发送的值（-1 表示初始状态）
unsigned long lastPostTime = 0;

// ── WiFi 连接 ──
void connectWiFi() {
  Serial.print("WiFi connecting");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[OK] WiFi connected");
    Serial.print("     IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[FAIL] WiFi failed, will retry...");
  }
}

// ── POST JSON 到 VPS ──
bool sendTouch(int pressure) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(VPS_URL);
  http.addHeader("Content-Type", "application/json");

  String json = "{\"pressure\":" + String(pressure) + ",\"sensor\":\"fsr402_1\"}";
  int code = http.POST(json);
  http.end();

  return code > 0 && code < 400;
}

// ── Setup ──
void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);  // 0 ~ 4095

  Serial.println("\n=== FSR402 WiFi Sensor ===");
  Serial.print("Pin: GPIO14 | Threshold: ±");
  Serial.print(POST_THRESH);
  Serial.print(" | Cooldown: ");
  Serial.print(POST_COOLDOWN);
  Serial.println("ms");
  Serial.print("VPS: ");
  Serial.println(VPS_URL);

  if (strlen(WIFI_SSID) == 0) {
    Serial.println("\n⚠ 请先填写 WIFI_SSID / WIFI_PASS / VPS_URL 再烧录！");
    while (1) { delay(1000); }
  }

  connectWiFi();
}

// ── Loop ──
void loop() {
  // WiFi 断了就重连
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] lost, reconnecting...");
    connectWiFi();
    delay(1000);
    return;
  }

  int raw = analogRead(FSR_PIN);
  unsigned long now = millis();

  // 是否需要发送？
  bool shouldPost = false;
  if (lastValue == -1) {
    // 首次读数，无论如何发
    shouldPost = true;
  } else if (abs(raw - lastValue) >= POST_THRESH) {
    shouldPost = true;
  }

  // 冷却检查
  if (shouldPost && now - lastPostTime < POST_COOLDOWN) {
    shouldPost = false;
  }

  if (shouldPost) {
    bool ok = sendTouch(raw);
    lastValue = raw;
    lastPostTime = now;

    if (ok) {
      Serial.print("[POST] pressure: ");
      Serial.print(raw);
      Serial.print("  →  OK");
    } else {
      Serial.print("[POST] pressure: ");
      Serial.print(raw);
      Serial.print("  →  FAIL (code=");
      Serial.print(WiFi.status() == WL_CONNECTED ? "HTTP err" : "WiFi down");
      Serial.print(")");
    }

    // ASCII 力度条
    int n = raw * 20 / 4095;
    Serial.print("  |");
    for (int i = 0; i < n; i++) Serial.print("=");
    for (int i = n; i < 20; i++) Serial.print("-");
    Serial.println("|");
  }

  delay(READ_INTERVAL);
}
