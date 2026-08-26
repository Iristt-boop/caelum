"""
Use MicroPython raw REPL to run FSR402 ADC test on ESP32-S3.
Raw REPL protocol: Ctrl-A enter, send exec(bytes_repr), read output, Ctrl-B exit.
"""
import serial
import time
import sys

PORT = "COM4"
BAUD = 115200

# The MicroPython script (100 iterations, ~20 seconds)
SCRIPT = """\
from machine import ADC, Pin
from time import sleep_ms

adc = ADC(Pin(14))
adc.atten(ADC.ATTN_11DB)

print("FSR402 ADC Test - GPIO14")
print("Press the sensor now!")
print("")

for i in range(100):
    raw = adc.read()
    pct = raw / 4095.0 * 100.0
    n = int(pct / 5)
    bar = "=" * n + "-" * (20 - n)
    print("{:4d} [{:5.1f}%] |{}|".format(raw, pct, bar))
    sleep_ms(200)

print("")
print("=== Done ===")
"""

def main():
    s = serial.Serial(PORT, BAUD, timeout=2)
    time.sleep(0.3)

    # --- Step 1: Exit any weird modes ---
    s.write(b"\x02")  # Ctrl-B exits raw REPL
    time.sleep(0.2)
    s.write(b"\r\n\x03\x03\r\n")  # Ctrl-C interrupts
    time.sleep(0.3)
    while s.in_waiting:
        s.read(s.in_waiting)

    # --- Step 2: Enter raw REPL ---
    s.write(b"\x01")  # Ctrl-A
    time.sleep(0.3)
    resp = s.read_until(b">")
    if b"raw REPL" not in resp:
        print(f"Unexpected response to Ctrl-A: {resp!r}")
        print("Trying again...")
        s.write(b"\x02\r\n\x03\r\n\x01")
        time.sleep(0.5)
        resp = s.read_until(b">")
    print(f"Raw REPL: {resp!r}")

    # --- Step 3: Execute script ---
    payload = SCRIPT.encode()
    cmd = f"exec({payload!r})\r\n".encode()
    s.write(cmd)
    time.sleep(0.5)

    # --- Step 4: Read output ---
    print("\n--- ESP32 output ---")
    buf = b""
    deadline = time.time() + 30  # 30 sec timeout
    while time.time() < deadline:
        if s.in_waiting:
            chunk = s.read(s.in_waiting)
            buf += chunk
            # Print as we go
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
            # Check for end of raw REPL output: OK + \x04
            if b"\x04" in buf and b"OK" in buf:
                break
        time.sleep(0.02)

    # --- Step 5: Exit raw REPL ---
    s.write(b"\x02")  # Ctrl-B
    time.sleep(0.2)
    while s.in_waiting:
        s.read(s.in_waiting)

    s.close()
    print("\n--- Done ---")

if __name__ == "__main__":
    main()
