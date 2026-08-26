# FSR402 ADC Test for ESP32-S3 MicroPython
# Pin: GPIO14 (ADC2_CH3)
from machine import ADC, Pin
from time import sleep_ms

adc = ADC(Pin(14))
adc.atten(ADC.ATTN_11DB)

print("=== FSR402 Pressure Sensor ===")
print("Pin: GPIO14 (ADC2_CH3)")
print("Press the sensor now!")
print("")

while True:
    v = adc.read()
    p = v / 4095.0 * 100.0
    n = int(p / 5)
    bar = "=" * n + "-" * (20 - n)
    print("raw:{:4d}  pressure:{:5.1f}%  |{}|".format(v, p, bar))
    sleep_ms(200)
