"""
FSR402 压力传感器监控 — 串口监视器
先运行 upload.py 上传脚本，再运行本文件监视输出。
或直接用: python -m ampy.cli --port COM4 run fsr_test_mp.py
"""
import serial
import time
import sys

PORT = "COM4"
BAUD = 115200

s = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(0.3)

# 中断 + 软复位
s.write(b"\r\n\x03\r\n")
time.sleep(0.2)
while s.in_waiting:
    s.read(s.in_waiting)
s.write(b"\x04")
time.sleep(2)
while s.in_waiting:
    s.read(s.in_waiting)

print("=== FSR402 压力传感器 ===")
print("按 Ctrl+C 停止\n")

try:
    while True:
        if s.in_waiting:
            chunk = s.read(s.in_waiting)
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
        time.sleep(0.02)
except KeyboardInterrupt:
    print("\n停止中...")
    s.write(b"\r\n\x03\r\n")
    time.sleep(0.2)
    s.close()
    print("已断开")
