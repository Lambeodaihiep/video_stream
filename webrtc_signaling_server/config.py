from aiortc import RTCIceServer

##### CONFIG CHUNG #####

# ====== UDP multicast ======
# ====== UDP ======
udp_unicast_port = 40005
udp_multicast_group = "232.4.130.147"
udp_multicast_telemetry_port = 40004
eth0_ip_address = "192.168.0.11"

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
PC_RETRY_TIME = 1

ice_servers = [
    #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
    #RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
    RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
]


##### CONFIG CHO PUBLISHER #####



##### CONFIG CHO VIEWER
