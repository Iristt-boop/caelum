"""Send ADC test to ESP32-S3 MicroPython REPL and relay output."""
import serial
import time
import sys

PORT = "COM4"
BAUD = 115200

# Open serial
print(f"Connecting to {PORT}...")
s = serial.Serial(PORT, BAUD, timeout=2)
time.sleep(0.5)

# Flush & exit any weird modes
s.write(b"\x02")  # Ctrl-B: exit raw REPL
time.sleep(0.3)
while s.in_waiting:
    s.read(s.in_waiting)

# Ensure clean REPL
s.write(b"\r\n\x03\r\n")
time.sleep(0.5)
while s.in_waiting:
    s.read(s.in_waiting)

print("Entering paste mode...")
# Ctrl-E paste mode
s.write(b"\x05")
time.sleep(0.5)
while s.in_waiting:
    s.read(s.in_waiting)

# The script to paste
script = """from machine import ADC, Pin, Timer
import time

adc = ADC(Pin(14))
adc.atten(ADC.ATTN_11DB)

print("=== FSR402 ADC Test ===")
print("Pin: GPIO14 (ADC2_CH3)")

while True:
    raw = adc.read()
    pct = raw / 4095.0 * 100.0
    bar_len = int(pct / 5)
    bar = "=" * bar_len + "-" * (20 - bar_len)
    print("raw:{:4d}  pressure:{:5.1f}%  |{}|".format(raw, pct, bar))
    time.sleep(0.2)
"""

s.write(script.encode())
time.sleep(0.5)

# Ctrl-D to execute
s.write(b"\x04")
time.sleep(1)

print("Script started! Press Ctrl+C to stop.\n")

try:
    while True:
        if s.in_waiting:
            chunk = s.read(s.in_waiting)
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
        time.sleep(0.01)
except KeyboardInterrupt:
    print("\nStopping...")
    s.write(b"\r\n\x03\x03\x03\r\n")
    time.sleep(0.3)
    s.close()
    print("Done.")
