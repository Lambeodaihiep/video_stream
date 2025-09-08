from aiortc import RTCIceServer

##### CONFIG CHUNG #####

# ====== SIGNALING SERVER ======
#SIGNALING_SERVER = "ws://dev.bitsec.it:8889"
SIGNALING_SERVER = "ws://192.168.50.243:8889"

# ====== TURN SERVER ======
TURN_HOST     = "dev.bitsec.it:3478"       # "198.51.100.22:3478"
TURN_USER     = "nhatdn"
TURN_PASS     = "123456"

# thời gian thiết lập lại peer connection
PC_RETRY_TIME = 1

ice_servers = [
    #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
    #RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
    RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
]


##### CONFIG CHO PUBLISHER #####



##### CONFIG CHO VIEWER
