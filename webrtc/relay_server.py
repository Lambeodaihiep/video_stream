import argparse

import asyncio
import json
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

from aiortc.codecs import get_capabilities

# cái này để ép server dùng codec h264
video_caps = get_capabilities("video")
h264_codecs = [c for c in video_caps.codecs if c.mimeType.lower() == "video/h264"]

# khai báo các biến cần thiết
pcs = set()             # giữ tất cả các peer connection
channels = set()        # giữ tất cả data channels để broadcast
viewers = set()         # giữ writer của viewer để báo sự kiện
relay = MediaRelay()    # chuyển tiếp video cho viewer
publisher_tracks = []   # lưu danh sách track do publisher gửi (ví dụ video)
publisher_connected = False

# ====== TURN SERVER ======
TURN_HOST     = "192.168.0.117:3478"       # "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"

# ====== signaling helpers ======
async def send_json(writer, obj):
    writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()

async def recv_json(reader):
    line = await reader.readline()
    if not line:
        return None
    return json.loads(line.decode())

# ====== broadcast message tới tất cả channel ======
def broadcast(message, sender=None):
    for ch in list(channels):
        if ch != sender and ch.readyState == "open":
            ch.send(message)

# ====== handle publisher ======
async def handle_publisher(reader, writer):
    global publisher_tracks, publisher_connected
    ice_servers = [
        #RTCIceServer(urls=["stun.l.google.com:19302"])
        RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
        RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
    ]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    pcs.add(pc)

    # Nhận track từ publisher
    @pc.on("track")
    def on_track(track):
        print(f"[Publisher] Got track")
        publisher_tracks.clear()
        publisher_tracks.append(track)
        # publisher_tracks.append(relay.subscribe(track))

    # Nhận data channel từ publisher
    @pc.on("datachannel")
    def on_datachannel(channel):
        print("[Publisher] Data channel opened:", channel.label)
        channels.add(channel)

        @channel.on("message")
        def on_message(msg):
            print("[Publisher->All]:", msg)
            #broadcast(f"Publisher: {msg}", sender=channel)
            broadcast(msg, sender=channel)

    # nhận offer từ publisher
    offer = await recv_json(reader)
    # print(offer["sdp"])
    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer["sdp"], type=offer["type"]))

    # tạo answer gửi lại
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    # print(answer.sdp)
    await send_json(writer, {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp})

    print("[Publisher] Connected, waiting for media...")
    publisher_connected = True
    while True:
        msg = await recv_json(reader)
        if msg is None:
            break

    await pc.close()
    pcs.discard(pc)
    publisher_tracks.clear()
    publisher_connected = False
    print("[Publisher] Disconnected")
    
    # Báo cho tất cả viewer
    for viewer in list(viewers):
        await send_json(viewer, {"event": "publisher_disconnected"})
    viewers.clear()     # xoá tất cả writer

# ====== handle viewer ======
async def handle_viewer(reader, writer):
    global publisher_tracks, publisher_connected
    
    ice_servers = [
        #RTCIceServer(urls=["stun.l.google.com:19302"])
        RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
        RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
    ]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    #pc = RTCPeerConnection()
    pcs.add(pc)

    if not publisher_connected:
        # báo viewer biết chưa có stream
        await send_json(writer, {"event": "no_publisher"})
        writer.close()
        await writer.wait_closed()
        print("[Viewer] rejected: no publisher yet")
        return
    else:
        # thêm track từ publisher vào viewer
        for t in publisher_tracks:
            # pc.addTrack(t)
            pc.addTrack(relay.subscribe(t))
        
        viewers.add(writer)

    transceiver = pc.getTransceivers()[0]
    transceiver.setCodecPreferences(h264_codecs)

    # Tạo data channel "chat" cho viewer
    dc = pc.createDataChannel("chat")
    channels.add(dc)

    @dc.on("message")
    def on_message(msg):
        print("[Viewer->All]:", msg)
        broadcast(f"Viewer: {msg}", sender=dc)

    # tạo offer gửi cho viewer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await send_json(writer, {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp})

    # nhận answer từ viewer
    msg = await recv_json(reader)
    if msg:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))
        print("[Viewer] Answer received")

    while True:
        msg = await recv_json(reader)
        if msg is None:
            break

    await pc.close()
    pcs.discard(pc)
    viewers.discard(writer)
    print("[Viewer] Disconnected")

# ====== server main loop ======
async def handle_client(reader, writer):
    first = await recv_json(reader)
    if not first or "role" not in first:
        print("Invalid first message, closing")
        writer.close()
        return

    role = first["role"]
    if role == "publisher":
        await handle_publisher(reader, writer)
    elif role == "viewer":
        await handle_viewer(reader, writer)
    else:
        print("Unknown role:", role)
        writer.close()

async def main():
    parser = argparse.ArgumentParser(
        description="WebRTC video / data-channels implementation"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8889, help="Port for HTTP server (default: 8889)"
    )
    args = parser.parse_args()
    
    server = await asyncio.start_server(handle_client, args.host, args.port)
    print("Server running on " + args.host + ":" + str(args.port))
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
