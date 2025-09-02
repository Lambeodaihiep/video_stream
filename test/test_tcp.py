# server.py
import socket

HOST = '0.0.0.0'  # nhận mọi địa chỉ
PORT = 12345

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))
s.listen(1)

conn, addr = s.accept()
print(f"Kết nối từ {addr}")
while True:
    data = conn.recv(1024)
    if not data:
        break
    print("Nhận:", data.decode())

conn.close()