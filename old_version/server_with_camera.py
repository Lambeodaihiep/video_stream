import asyncio
import json
import cv2
import fractions
from datetime import datetime

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import VideoFrame

# ====== Video source (một nguồn dùng chung) ======
class OpenCVCameraTrack(VideoStreamTrack):
    """
    Track đọc từ một camera OpenCV. Được subscribe qua MediaRelay để chia sẻ cho nhiều client.
    """
    kind = "video"

    def __init__(self, camera_id=0, fps=30):
        super().__init__()
        self.cap = cv2.VideoCapture(camera_id)
        # Khuyến nghị: thiết lập FPS/size nếu cần (tùy camera)
        # self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.frame_count = 0
        self.time_base = fractions.Fraction(1, fps)
        self.fps = fps

        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera id={camera_id}")

    async def recv(self):
        # Gọi theo nhịp time_base của aiortc
        await asyncio.sleep(float(self.time_base))

        self.frame_count += 1
        ok, frame = self.cap.read()
        if not ok or frame is None:
            # Không đọc được frame -> chờ một chút và retry
            await asyncio.sleep(0.05)
            return await super().recv()

        # Thêm timestamp để debug timing
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        cv2.putText(frame, ts, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 255, 0), 2, cv2.LINE_AA)

        # BGR -> RGB
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        vf = VideoFrame.from_ndarray(frame, format="rgb24")
        vf.pts = self.frame_count
        vf.time_base = self.time_base
        return vf

# ====== Signaling helpers (JSON dòng qua TCP) ======
async def send_json(writer: asyncio.StreamWriter, obj: dict):
    data = (json.dumps(obj) + "\n").encode("utf-8")
    writer.write(data)
    await writer.drain()

async def recv_json(reader: asyncio.StreamReader):
    line = await reader.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8"))

# ====== Server logic ======
pcs = set()
relay = MediaRelay()        # Phát lại 1 nguồn cho nhiều subscriber
source_track = None         # Sẽ khởi tạo trong main()

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """
    Mỗi client TCP sẽ được xử lý trong coroutine này (chạy song song).
    Giao thức:
      - Server tạo offer, gửi {"type":"offer","sdp":...}
      - Client trả về {"type":"answer","sdp":...}
      - Khi client đóng socket -> đóng PeerConnection tương ứng
    """
    peer_addr = writer.get_extra_info("peername")
    print(f"[+] New TCP client from {peer_addr}")

    pc = RTCPeerConnection()
    pcs.add(pc)

    # Mỗi client subscribe từ cùng một nguồn camera
    global source_track, relay
    pc.addTrack(relay.subscribe(source_track))

    @pc.on("connectionstatechange")
    async def on_state_change():
        print(f"[{peer_addr}] PC state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            pcs.discard(pc)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            print(f"[{peer_addr}] PC closed & cleaned")

    try:
        # Tạo offer & gửi cho client
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await send_json(writer, {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp})
        print(f"[{peer_addr}] Sent SDP offer")

        # Đợi answer
        msg = await recv_json(reader)
        if not msg:
            print(f"[{peer_addr}] No answer received, closing")
            return
        if msg.get("type") != "answer" or "sdp" not in msg:
            print(f"[{peer_addr}] Invalid answer payload: {msg}")
            return

        await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg["type"]))
        print(f"[{peer_addr}] Remote description applied (answer)")

        # (Không dùng trickle ICE) – không cần vòng nhận candidate.
        # Giữ TCP mở cho tới khi client đóng (hoặc PC chuyển sang disconnected/failed).
        while True:
            msg = await recv_json(reader)
            if msg is None:
                print(f"[{peer_addr}] TCP closed by client")
                break
            # Có thể mở rộng: nhận BYE / control messages ở đây
            # print(f"[{peer_addr}] Ignored message: {msg}")

    except Exception as e:
        print(f"[{peer_addr}] Exception: {e}")

    finally:
        try:
            await pc.close()
        except Exception:
            pass
        pcs.discard(pc)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        print(f"[-] Client finished {peer_addr}, active PCs: {len(pcs)}")

async def run_server(host="0.0.0.0", port=8889, camera_id=0):
    global source_track
    # Khởi tạo 1 nguồn camera duy nhất
    source_track = OpenCVCameraTrack(camera_id=camera_id, fps=30)
    print(f"[Camera] Opened camera id={camera_id}")

    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"[Signaling] Serving on {addrs}")

    async with server:
        await server.serve_forever()

def main():
    host = "0.0.0.0"
    port = 8889
    camera_id = 0  # đổi nếu cần
    try:
        asyncio.run(run_server(host, port, camera_id))
    except KeyboardInterrupt:
        print("Interrupted, shutting down...")

if __name__ == "__main__":
    main()
