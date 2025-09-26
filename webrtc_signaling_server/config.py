from aiortc import RTCIceServer

##### CONFIG CHUNG #####

# ====== UDP multicast ======
# ====== UDP ======
udp_multicast_telemetry_address = "232.4.130.147"
udp_multicast_telemetry_port = 40002

# ====== SIGNALING SERVER ======
# SIGNALING_SERVER = "ws://dev.bitsec.it:8889"
SIGNALING_SERVER = "ws://171.224.83.50:8889"

# ====== TURN SERVER ======
# TURN_HOST     = "dev.bitsec.it:3478"       # "198.51.100.22:3478"
# TURN_USER     = "nhatdn"
# TURN_PASS     = "123456"

TURN_HOST     = "171.224.83.50:3478"       # "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"

# thời gian thiết lập lại peer connection
PC_RETRY_TIME = 2

ice_servers = [
    #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
    #RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
    RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
]


##### CONFIG CHO PUBLISHER #####
eth0_ip_address = "192.168.1.111"
udp_unicast_video_port = 40005

udp_unicast_command_address = "192.168.1.14"
udp_unicast_command_port = 40001

udp_multicast_video_address = "225.1.2.3"
udp_multicast_video_port = 11024

##### CONFIG CHO VIEWER
