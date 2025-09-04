import argparse
import serial

import asyncio
import json
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCRtpSender
from aiortc import RTCConfiguration, RTCIceServer
from av import VideoFrame
from aiortc.contrib.media import MediaPlayer
import fractions
from datetime import datetime

from aiortc.codecs import get_capabilities

# cái này để ép server dùng codec h264
video_caps = get_capabilities("video")
h264_codecs = [c for c in video_caps.codecs if c.mimeType.lower() == "video/h264"]

# ====== TURN SERVER ======
TURN_HOST     = "192.168.0.117:3478"       # "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"
RETRY_TIME = 3

# ====== signaling helpers ======
async def send_json(writer, obj):
    writer.write((json.dumps(obj) + "\n").encode())
    await writer.drain()

async def recv_json(reader, timeout=None):
    try:
        if timeout:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        else:
            line = await reader.readline()
    except asyncio.TimeoutError:
        return None
    if not line:
        return None
    return json.loads(line.decode())

# ====== VideoTrack từ OpenCV ====== (not use)
class OpenCVCameraTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, camera_id=0, fps=30):
        super().__init__()
        self.cap = cv2.VideoCapture(camera_id)
        self.frame_count = 0
        self.time_base = fractions.Fraction(1, fps)

    async def recv(self):
        self.frame_count += 1
        ret, frame = self.cap.read()
        if not ret:
            await asyncio.sleep(0.1)
            return await super().recv()

        frame = cv2.resize(frame, (1080, 720))
        # thêm timestamp
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        cv2.putText(frame, ts, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 255, 0), 2, cv2.LINE_AA)

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        vf = VideoFrame.from_ndarray(frame, format="rgb24")
        vf.pts = self.frame_count
        vf.time_base = self.time_base
        return vf

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
            #print("Sent:", msg)
        await asyncio.sleep(10)  # gửi mỗi vài giây

# ====== run publisher ======
async def run_publisher(host: str, port: int, COM_PORT: str, baudrate: int, timeout: int):
    pc = None
    player = None
    writer = None
    try:
        # URL RTSP của camera IP
        rtsp_url = "rtsp://192.168.0.100:8080/h264.sdp"
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

        # tạo kết nối tới server webrtc
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout
        )
        await send_json(writer, {"role": "publisher"})

        ice_servers = [
            #RTCIceServer(urls=["stun:stun.l.google.com:19302"])
            RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
            RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
        ]
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        
        # kiểm tra tình trạng kết nối
        @pc.on("connectionstatechange")
        async def on_state_change():
            print("[Publisher] state:", pc.connectionState)
            if pc.connectionState in ("failed", "disconnected", "closed"):
                raise ConnectionError("PeerConnection lost")

        # Lấy track video từ RTSP (H.264) hoặc frame từ camera
        #pc.addTrack(OpenCVCameraTrack(camera_id=0))
        if player.video:
            print("hehehehe")
            pc.addTrack(player.video)
            transceiver = pc.getTransceivers()[0]
            transceiver.setCodecPreferences(h264_codecs)
        else:
            print("No video track from RTSP!")
            return

        # tạo data channel chat
        channel = pc.createDataChannel("chat")

        @channel.on("open")
        def on_open():
            print("[Publisher] Chat channel opened")
            asyncio.ensure_future(send_periodic(channel))
            #asyncio.ensure_future(uart_reader(channel, COM_port, baudrate))

        # Nhận được tin nhắn thì in ra
        @channel.on("message")
        def on_message(msg):
            print("[Publisher] Received:", msg)

        # tạo offer gửi cho server
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await send_json(writer, {"type": offer.type, "sdp": offer.sdp})

        # nhận answer từ server
        msg = await recv_json(reader, timeout=10)
        if not msg:
            print("[Publisher] Timeout chờ answer")
            return "retry"
        await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))

        # giữ kết nối
        while True:
            msg = await recv_json(reader)
            if msg is None:
                raise ConnectionError("Server lost")
            await asyncio.sleep(0)
    
    except asyncio.TimeoutError:
        print(f"Timeout {timeout}s, could not connect to {host}:{port}")
        return "retry"

    except Exception as e:
        print("[Publisher] Error:", e)
        return "retry"
    
    finally:
        if pc:
            await pc.close()
        if player:
            player.video.stop()
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

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
        result = await run_publisher(args.host, args.port, args.COM_port, args.baudrate, args.timeout)
        if result in ["retry", "no_camera"]:
            if result == "retry":
                print("Server not available, try to connect ...")
            await asyncio.sleep(RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break

if __name__ == "__main__":
    asyncio.run(main())
