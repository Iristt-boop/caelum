/*
 * FSR402 压力传感器测试
 * 板子：ESP32-S3 N16R8
 * 引脚：GPIO14 (ADC2_CH3)
 *
 * 接线（免焊接）：
 *   FSR402 左腿 → 面包板 A 列某行 → 跳线 → ESP32 3.3V
 *   FSR402 右腿 → 面包板 B 列某行 → 跳线 → GPIO14
 *   10KΩ 电阻 → 一头接 FSR402 右腿那条竖线，另一头接 GND
 *
 *   口诀：左腿供电，右腿出信号 + 过电阻到地
 */

const int FSR_PIN = 14;          // GPIO14 — ADC2_CH3
const int READ_INTERVAL = 200;   // 每 200ms 读一次

unsigned long lastRead = 0;

void setup() {
  Serial.begin(115200);
  delay(500);  // 等串口就绪

  analogReadResolution(12);  // 0 ~ 4095

  Serial.println("=== FSR402 Test ===");
  Serial.println("Board: ESP32-S3 N16R8");
  Serial.println("Pin:   GPIO14 (ADC2_CH3)");
  Serial.println("按下 Ctrl+C 或关闭窗口停止\n");
}

void loop() {
  unsigned long now = millis();

  if (now - lastRead >= READ_INTERVAL) {
    lastRead = now;

    int raw = analogRead(FSR_PIN);
    float percent = raw / 4095.0 * 100.0;

    // 原始值 + 百分比 + 简易力度条
    Serial.print("raw: ");
    Serial.print(raw);
    Serial.print("\t");

    Serial.print("pressure: ");
    Serial.print(percent, 1);  // 保留一位小数
    Serial.print("%\t");

    // 简易 ASCII 力度条（每格 ≈ 5%）
    int bar = (int)(percent / 5);
    Serial.print("|");
    for (int i = 0; i < bar; i++) {
      Serial.print("=");
    }
    for (int i = bar; i < 20; i++) {
      Serial.print("-");
    }
    Serial.println("|");
  }
}
