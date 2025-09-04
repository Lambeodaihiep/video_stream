import argparse
import serial

import asyncio
import json
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc import RTCConfiguration, RTCIceServer
from av import VideoFrame
import fractions
from datetime import datetime

# ====== TURN SERVER ======
TURN_HOST     = "117.5.76.131:3478"       # "198.51.100.22:3478"
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

# ====== VideoTrack từ OpenCV ======
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

# ====== read from uart ======
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
    args = parser.parse_args()

    reader, writer = await asyncio.open_connection(args.host, args.port)
    await send_json(writer, {"role": "publisher"})

    ice_servers = [
        RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
        RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
    ]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    pc.addTrack(OpenCVCameraTrack(camera_id=0))

    # tạo data channel chat
    channel = pc.createDataChannel("chat")

    @channel.on("open")
    def on_open():
        print("[Publisher] Chat channel opened")

        async def send_periodic():
            while True:
                if channel.readyState == "open":
                    msg = b"\x01\x08\xA5\x5B\x68"
                    channel.send(msg)
                    print("Sent:", msg)
                await asyncio.sleep(2)  # gửi mỗi vài giây

        asyncio.ensure_future(send_periodic())
        #asyncio.ensure_future(uart_reader(channel, args.COM_port, args.baudrate))

    # Nhận được tin nhắn thì in ra
    @channel.on("message")
    def on_message(msg):
        print("[Publisher] Received:", msg)

    # tạo offer gửi cho server
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await send_json(writer, {"type": offer.type, "sdp": offer.sdp})

    # nhận answer từ server
    msg = await recv_json(reader)
    if msg:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))
        print("[Publisher] Connected to server")

    # giữ kết nối
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
