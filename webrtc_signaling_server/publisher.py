import argparse
import serial

import asyncio, websockets
import json
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCRtpSender
from aiortc import RTCConfiguration, RTCIceServer
from av import VideoFrame
from aiortc.contrib.media import MediaPlayer
import fractions
from datetime import datetime
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

from aiortc.codecs import get_capabilities

# cái này để ép server dùng codec h264
video_caps = get_capabilities("video")
h264_codecs = [c for c in video_caps.codecs if c.mimeType.lower() == "video/h264"]

SIGNALING_SERVER = "ws://dev.bitsec.it:8889"

# ====== TURN SERVER ======
TURN_HOST     = "dev.bitsec.it:3478"       # "198.51.100.22:3478"
TURN_USER     = "nhatdn"
TURN_PASS     = "123456"
RETRY_TIME = 3

# ====== Camera IP ======
rtsp_url = "rtsp://192.168.0.101:8080/h264.sdp"
# rtsp_url = "rtsp://admin:123456!Vht@192.168.1.120:18554/h264"

ice_servers = [
    #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
    #RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
    RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
]

# ====== đọc từ uart rồi gửi đi ====== (not use)
async def uart_reader(channel, COM_port, baudrate):
    ser = serial.Serial(COM_port, baudrate=baudrate, timeout=1)
    print(f"[Publisher] UART opened at {COM_port} {baudrate}")
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, ser.readline)
        if line:
            msg = line.decode(errors="ignore").strip()
            #data = {"from": "publisher", "to": "viewer-1", "msg": msg}
            if channel.readyState == "open":
                #channel.send(json.dumps(data))
                channel.send(msg)
                print("[Publisher] Sent UART ->", msg)
            else:
                print("[Publisher] No channel to send")

# ====== Gửi dữ liệu linh tinh mỗi vài giây ======
async def send_periodic(channel):
    while True:
        if channel.readyState == "open":
            msg = b"\x01\x08\xA5\x5B\x68"
            channel.send(msg)
        await asyncio.sleep(2)  # gửi mỗi vài giây

async def run(host: str, port: int, COM_PORT: str, baudrate: int, timeout: int):
    pc = None
    player = None
    try:
        # kiểm tra RTSP camera bằng opencv trước
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            print("RTSP camera not available. Try again ...")
            return "no_camera"
        cap.release()

        # Tùy chọn FFmpeg để ổn định & giảm trễ
        # - rtsp_transport: "tcp" (ổn định) hoặc "udp" (độ trễ thấp hơn nếu mạng tốt)
        # - stimeout: timeout socket (microseconds)
        # - fflags=nobuffer/flags=low_delay: giảm đệm
        player = MediaPlayer(
            rtsp_url,
            format="rtsp",
            options={
                "rtsp_transport": "udp",
                "stimeout": "5000000",
                "fflags": "nobuffer",
                "flags": "low_delay",
                "max_delay": "0",
                "framedrop": "1",
            },
            decode=False
        )

        # Tạo peer connection
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

        # kiểm tra tình trạng kết nối
        @pc.on("connectionstatechange")
        async def on_state_change():
            print("[Publisher] state:", pc.connectionState)
            if pc.connectionState in ("failed", "disconnected", "closed"):
                raise ConnectionError("PeerConnection lost")

        # thêm track để gửi đi
        if player.video:
            print("hehehehe")
            pc.addTrack(player.video)
            # Ép server codec h264
            transceiver = pc.getTransceivers()[0]
            transceiver.setCodecPreferences(h264_codecs)
        else:
            print("No video track from RTSP!")
            return "no_camera"

        # Tạo kênh gửi dữ liệu
        channel = pc.createDataChannel("chat")
        @channel.on("open")
        def on_open():
            print("Channel opened")
            asyncio.ensure_future(send_periodic(channel))
            # asyncio.ensure_future(uart_reader(channel, COM_port, baudrate))

        @channel.on("message")
        def on_message(message):
            print(f"Got from subscriber: {message}")

        async with websockets.connect(SIGNALING_SERVER) as ws:
            # gửi offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await ws.send(json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}))

            # nhận answer
            async for msg in ws:
                data = json.loads(msg)
                if data["type"] == "answer":
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="answer"))
                    print("Remote description set (answer)")
                elif data["type"] == "candidate":
                    # nhận ICE candidate từ viewer
                    candidate = candidate_from_sdp(data["candidate"].split("\n")[0])
                    pc.addIceCandidate(candidate)

            # gửi ICE của publisher
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
        if player:
            player.video.stop()


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
        "--COM_port", default="/dev/ttyUSB0"
    )
    parser.add_argument(
        "--baudrate", type=int, default=115200
    )
    parser.add_argument(
        "--timeout", type=int, default=3, help="Server connecting timeout (default: 3s)"
    )
    args = parser.parse_args()

    while True:
        result = await run(args.host, args.port, args.COM_port, args.baudrate, args.timeout)
        if result in ["retry", "no_camera"]:
            if result == "retry":
                print("Server not available, try to connect ...")
            await asyncio.sleep(RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break


if __name__ == "__main__":
    asyncio.run(main())