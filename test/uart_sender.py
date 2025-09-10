import serial
import time

ser = serial.Serial("/dev/ttyACM2", baudrate=115200, timeout=1)

i = 0
while True:
    msg = f"Data {i}\n"s
    ser.write(msg.encode())
    print("Sent:", msg.strip())
    i += 1
    time.sleep(1)
    

