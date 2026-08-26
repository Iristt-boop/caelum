# 临时读数测试：6 个传感器一起读
# GPIO3 / GPIO4 是新接的（带力度条），其余给数字做基准
from machine import ADC, Pin
from time import sleep_ms

pins = [3, 4, 9, 11, 13, 14]
adcs = []
for p in pins:
    a = ADC(Pin(p))
    a.atten(ADC.ATTN_11DB)
    adcs.append(a)

print("=== 读数测试 (6个) ===")
print("GPIO3  GPIO4  GPIO9  GPIO11  GPIO13  GPIO14")
print("")

while True:
    vals = [a.read() for a in adcs]

    def bar(v):
        n = v * 20 // 4095
        return "=" * n + "-" * (20 - n)

    print("3:{} |{}|  4:{} |{}|   9:{}  11:{}  13:{}  14:{}".format(
        vals[0], bar(vals[0]), vals[1], bar(vals[1]),
        vals[2], vals[3], vals[4], vals[5]))
    sleep_ms(200)
