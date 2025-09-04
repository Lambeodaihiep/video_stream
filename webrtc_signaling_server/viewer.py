from aiortc.sdp import candidate_from_sdp, candidate_to_sdp
import argparse
import socket

import asyncio, websockets
import json
import cv2
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRecorder
from aiortc.rtp import RtpPacket

SIGNALING_SERVER = "ws://dev.bitsec.it:8889"

# ====== TURN SERVER ======
TURN_HOST     = "dev.bitsec.it:3478"       # "198.51.100.22:3478"
TURN_USER     = "nhatdn"
TURN_PASS     = "123456"

# ====== GCS PORT ======
VIDEO_PORT = 5001
TELEMETRY_PORT = 14450
RETRY_TIME = 3

ice_servers = [
    #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
    #RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
    RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
]

# ====== hiển thị bằng opencv ======
async def opencv_display(track):
    while True:
        frame = await track.recv()
        img = frame.to_ndarray(format="bgr24")
        cv2.imshow("Viewer", img)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()

# ====== Gửi hết rtp packet qua UDP ======
async def send_rtp_packet_udp(track, udp_sock: socket.socket, GCS_IP: str):
    while True:
        rtp_packet = await track.recv_rtp_packet()
        # print(len(rtp_packet._data))
        try:
            udp_sock.sendto(rtp_packet._data, (GCS_IP, VIDEO_PORT))
        except Exception as e:
            print("UDP send error: ", e)

async def run(host: str, port: int, GCS_IP: str, timeout: int):
    pc = None
    try:
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

        # kiểm tra tình trạng kết nối
        @pc.on("connectionstatechange")
        async def on_state_change():
            print("[Viewer] state:", pc.connectionState)
            if pc.connectionState in ("failed", "disconnected", "closed"):
                return "retry"
                raise ConnectionError("PeerConnection lost")
            
        # ====== mở udp để gửi dữ liệu ======
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        @pc.on("datachannel")
        def on_datachannel(channel):
            print("Data channel received:", channel.label)

            @channel.on("message")
            def on_message(message):
                print(f"Got from publisher: {message}")
                channel.send(f"Hello from subsciber")

                #print("[Viewer] Received:", msg)
                # try:
                #     if not isinstance(msg, bytes):
                #         data = msg.tobytes()
                #         udp_sock.sendto(data, (GCS_IP, TELEMETRY_PORT))
                #     else:
                #         udp_sock.sendto(msg, (GCS_IP, TELEMETRY_PORT))
                # except Exception as e:
                #     print("UDP send error: ", e)

        # nhận được track video thì hiển thị hoặc là gửi cho thiết bị khác qua udp
        @pc.on("track")
        def on_track(track):
            print("[Viewer] Track received, track kind: ", track.kind)
            # asyncio.ensure_future(opencv_display(track))
            asyncio.ensure_future(send_rtp_packet_udp(track, udp_sock, GCS_IP))

        async with websockets.connect(SIGNALING_SERVER) as ws:
            async for msg in ws:
                data = json.loads(msg)
                if data["type"] == "offer":
                    # nhận offer từ publisher
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
                    # tạo answer
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    await ws.send(json.dumps({"type":"answer","sdp":pc.localDescription.sdp}))
                elif data["type"] == "candidate":
                    candidate = candidate_from_sdp(data["candidate"].split("\n")[0])
                    pc.addIceCandidate(candidate)

            # gửi ICE của viewer
            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await ws.send(json.dumps({
                        "type":"candidate",
                        "candidate": candidate_to_sdp(candidate).split("\n")[0]
                    }))

    except Exception as e:
        print("Error:", e)
        return "retry"
    
    finally:
        if pc:
            await pc.close()

# ====== main ======
async def main():
    parser = argparse.ArgumentParser(
        description="WebRTC video / data-channels implementation"
    )
    parser.add_argument(
        "--host", default="117.5.76.131", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8889, help="Port for HTTP server (default: 8889)"
    )
    parser.add_argument(
        "--GCS_IP", default="127.0.0.1", help="GCS IP address to send data"
    )
    parser.add_argument(
        "--timeout", type=int, default=3, help="Server connecting timeout (default: 3s)"
    )
    args = parser.parse_args()

    while True:
        result = await run(args.host, args.port, args.GCS_IP, args.timeout)
        if result == "retry":
            await asyncio.sleep(RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break

if __name__ == "__main__":
    asyncio.run(main())