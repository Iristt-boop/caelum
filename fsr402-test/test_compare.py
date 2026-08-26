# 定位：GPIO3(有传感器) vs GPIO5(空引脚,没接) 对比
# 如果空引脚也规律跳 -> 电路/电源级问题
# 如果空引脚稳/随机 -> 是传感器被填充物压在半导通状态
from machine import ADC, Pin
from time import sleep_ms

adc3 = ADC(Pin(3))
adc3.atten(ADC.ATTN_11DB)
adc5 = ADC(Pin(5))
adc5.atten(ADC.ATTN_11DB)

print("=== 对比: GPIO3(传感器) vs GPIO5(空) ===")
print("")

while True:
    v3 = adc3.read()
    v5 = adc5.read()
    print("GPIO3:{:4d}   GPIO5(空):{:4d}".format(v3, v5))
    sleep_ms(200)
