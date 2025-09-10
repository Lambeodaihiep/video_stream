import serial
import time

ser0 = serial.Serial("/dev/ttyACM0", baudrate=115200, timeout=1)
ser1 = serial.Serial("/dev/ttyACM1", baudrate=115200, timeout=1)

ser0.flush()
ser1.flush()

i = 0
while True:
    if ser0.in_waiting > 0:
        line = ser0.read(ser0.in_waiting)#.decode(errors='ignore').strip()
        print(line)
        
    if ser1.in_waiting > 0:
        print("hihihihihi")
        line = ser1.readline().decode('utf-8', errors='ignore').strip()
        print(line)
        
ser0.close()
ser1.close()
print("close all serial")
        
# from pymavlink import mavutil

# # Kết nối tới Cube
# master = mavutil.mavlink_connection('/dev/ttyACM0', baud=115200)

# # Chờ heartbeat để đồng bộ
# master.wait_heartbeat()
# print("Heartbeat from system (system %u component %u)" %
      # (master.target_system, master.target_component))

# # Lấy dữ liệu attitude
# while True:
    # msg = master.recv_match(type='ATTITUDE', blocking=True)
    # print(msg)

