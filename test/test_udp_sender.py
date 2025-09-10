import socket
import time

sck = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

while True:
    sck.sendto(b"\x02\x56\xF5\x0D", ("127.0.0.1", 14450))
    print("sent sth")
    time.sleep(2)
    
sck.close()
    
