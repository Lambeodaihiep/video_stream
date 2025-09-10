import socket
import time

sck = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sck.bind(("127.0.0.1", 14450))

while True:
    packet, _ = sck.recvfrom(65535)
    print(f"Received: {len(packet)}")
    
sck.close()
    
