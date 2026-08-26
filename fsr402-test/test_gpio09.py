# 临时读数测试：GPIO9（新）+ GPIO11 + GPIO13 + GPIO14（已接好的）
from machine import ADC, Pin
from time import sleep_ms

adc9  = ADC(Pin(9))
adc9.atten(ADC.ATTN_11DB)
adc11 = ADC(Pin(11))
adc11.atten(ADC.ATTN_11DB)
adc13 = ADC(Pin(13))
adc13.atten(ADC.ATTN_11DB)
adc14 = ADC(Pin(14))
adc14.atten(ADC.ATTN_11DB)

print("=== 读数测试 ===")
print("GPIO9(新) | GPIO11 | GPIO13 | GPIO14")
print("")

while True:
    v9  = adc9.read()
    v11 = adc11.read()
    v13 = adc13.read()
    v14 = adc14.read()

    n9 = v9 * 20 // 4095
    bar9 = "=" * n9 + "-" * (20 - n9)

    print("GPIO9(新):{:4d} |{}|   11:{:4d}  13:{:4d}  14:{:4d}".format(v9, bar9, v11, v13, v14))
    sleep_ms(200)
