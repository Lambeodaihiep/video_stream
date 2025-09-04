import argparse
import socket

import asyncio
import json
import cv2
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer

# ====== TURN SERVER ======
TURN_HOST     = "117.5.76.131:3478"       # "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"

# ====== GCS PORT ======
VIDEO_PORT = 5001
TELEMETRY_PORT = 14450

# ====== signaling helpers ======
async def send_json(writer, obj):
    writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()

async def recv_json(reader):
    line = await reader.readline()
    if not line:
        return None
    return json.loads(line.decode())

# ====== main ======
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
    parser.add_argument(
        "--GCS_IP", default="0.0.0.0", help="GCS IP address to send data"
    )
    args = parser.parse_args()

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reader, writer = await asyncio.open_connection(args.host, args.port)
    await send_json(writer, {"role": "viewer"})

    ice_servers = [
        RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
        RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
    ]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    chat_channel = None

    @pc.on("datachannel")
    def on_datachannel(channel):
        nonlocal chat_channel
        chat_channel = channel
        print("[Viewer] Data channel received:", channel.label)

        # test gửi tin nhắn sau khi mở
        @channel.on("open")
        def on_open():
            channel.send("[Viewer] Chat channel opened, you can type messages...")

            #async def send_periodic():
            #    counter = 0
            #    while True:
            #        if channel.readyState == "open":
            #            msg = f"[Viewer] Hello {counter}"
            #            channel.send(msg)
            #            print("Sent:", msg)
            #            counter += 1
            #        await asyncio.sleep(3)  # gửi mỗi 3 giây
            #asyncio.ensure_future(send_periodic())
            
        # nhận được tin nhắn thì in ra và gửi cho thiết bị khác qua udp
        @channel.on("message")
        def on_message(msg):
            print("[Viewer] Received:", msg)
            try:
                if not isinstance(msg, bytes):
                    data = msg.tobytes()
                    udp_sock.sendto(data, (args.GCS_IP, TELEMETRY_PORT))
                else:
                    udp_sock.sendto(msg, (args.GCS_IP, TELEMETRY_PORT))
            except Exception as e:
                print("UDP send error: ", e)

    # nhận được track video thì hiển thị hoặc là gửi cho thiết bị khác qua udp
    @pc.on("track")
    def on_track(track):
        print("[Viewer] Track received:", track.kind)

        # async def display():
        #    while True:
        #        frame = await track.recv()
        #        img = frame.to_ndarray(format="bgr24")
        #        cv2.imshow("Viewer", img)
        #        if cv2.waitKey(1) & 0xFF == ord("q"):
        #            break
        #    cv2.destroyAllWindows()

        # asyncio.ensure_future(display())
        
        async def send_video_udp():
            while True:
                frame = await track.recv()
                img = frame.to_ndarray(format="bgr24")
                data = img.tobytes()
                try:
                    udp_sock.sendto(data, (args.GCS_IP, VIDEO_PORT))
                except Exception as e:
                    print("UDP send error: ", e)
                    
        asyncio.ensure_future(send_video_udp())
                
    # nhận offer từ server
    msg = await recv_json(reader)
    offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
    await pc.setRemoteDescription(offer)

    # tạo answer gửi lại server
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await send_json(writer, {"type": answer.type, "sdp": answer.sdp})
    print("[Viewer] Answer sent, streaming should start")

    # vòng lặp nhập console để gửi
    loop = asyncio.get_running_loop()
    # giữ kết nối
    while True:
        # nhập vào từ màn hình để gửi đi
        #msg = await loop.run_in_executor(None, input, "You> ")
        #if chat_channel and chat_channel.readyState == "open":
        #    chat_channel.send(msg)
        #else:
        #    print("[Viewer] Chat channel not ready yet")
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
