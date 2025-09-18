import socket
import time

MCAST_GRP = '232.4.130.147'
MCAST_PORT = 40004 

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

#message = 'hello from pi B, af'
message = b"\x01\x08\xA5\x5B\x68"

while True:
    sock.sendto(message, (MCAST_GRP, MCAST_PORT))
    print("sent sth to ...")
    time.sleep(1)
