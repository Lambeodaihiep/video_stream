import select
import time
import websockets, json, asyncio, serial, cv2, socket, subprocess, av
from aiortc.sdp import candidate_from_sdp
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, MediaStreamTrack
from aiortc.contrib.media import MediaPlayer

###### CÁC HÀM DÙNG CHUNG #####

# ====== Task giữ kết nối signaling server ======
async def signaling_loop(pc: RTCPeerConnection,
                         on_icecandidate,
                         role: str,
                         timeout: int,
                         signaling_server: str
    ):
    """
    Loop để duy trì kết nối với signaling server.
    Nếu WS rớt thì tự động reconnect.
    """
    while True:
        try:
            async with websockets.connect(signaling_server) as ws:
                print("Connected to signaling server")
                # gán ws cho handler ICE candidate
                on_icecandidate.ws = ws
                
                await ws.send(json.dumps({"role": role}))
                # nếu là publisher thì gửi offer
                if role == "publisher":
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)
                    await ws.send(json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}))

                async for msg in ws:
                    data = json.loads(msg)
                    if data["type"] == "offer":
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        await ws.send(json.dumps({
                            "type": "answer",
                            "sdp": pc.localDescription.sdp
                        }))
                    elif data["type"] == "answer":
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="answer"))
                    elif data["type"] == "candidate":
                        candidate = candidate_from_sdp(data["candidate"].split("\n")[0])
                        await pc.addIceCandidate(candidate)
                    elif data["type"] == "request_offer" and role == "publisher":
                        print("Server requested new offer")
                        offer = await pc.createOffer()
                        await pc.setLocalDescription(offer)
                        await ws.send(json.dumps({
                            "type": "offer",
                            "sdp": pc.localDescription.sdp
                        }))

        except Exception as e:
            print("Signaling connection error:", e)

        # đợi vài giây rồi thử reconnect lại signaling server
        await asyncio.sleep(timeout)

# ===== giữ kết nối với signaling server pro =====
async def signaling_loop_pro(pc: RTCPeerConnection,
                             lost_event: asyncio.Event,
                             on_icecandidate,
                             role: str,
                             timeout: int,
                             signaling_server: str
    ):
    """
    Trao đổi SDP/ICE
    Loop để duy trì kết nối với signaling server pro.
    Nếu WS rớt thì tự động reconnect.
    """
    ROOM = "testroom"
    url = f"{signaling_server}?room={ROOM}&peer={role}"
    print(f"[{role}] connecting to signaling {url}")
    while True:
        try:
            async with websockets.connect(url, ping_interval=None, close_timeout=timeout) as ws:
                # gắn ws vào on_icecandidate để gửi ICE
                on_icecandidate.ws = ws

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        print(f"[{role}] Bad signaling msg:", raw)
                        continue

                    t = msg.get("type")
                    if t == "peers":
                        peers = msg.get("peers", [])
                        print(f"[{role}] peers in room: {peers}")
                        # Publisher -> gửi offer tới peer đầu tiên
                        if role == "publisher" and peers:
                            target = peers[0]
                            offer = await pc.createOffer()
                            await pc.setLocalDescription(offer)
                            await ws.send(json.dumps({
                                "type": "offer",
                                "to": target,
                                "sdp": pc.localDescription.sdp,
                                "sdpType": pc.localDescription.type,
                            }))
                            print("sent offer")
                    elif t == "peer-joined":
                        peer = msg["peer"]
                        print(f"[{role}] Peer joined: {peer}")
                        if role == "publisher" and pc.signalingState == "stable":
                            # khi có viewer mới -> gửi offer
                            print("creating offer")
                            offer = await pc.createOffer()
                            print("created offer")
                            await pc.setLocalDescription(offer)
                            print("set offer")
                            await ws.send(json.dumps({
                                "type": "offer",
                                "to": peer,
                                "sdp": pc.localDescription.sdp,
                                "sdpType": pc.localDescription.type,
                            }))
                            print("sent offer")
                    elif t == "offer" and role == "viewer":
                        frm = msg["from"]
                        print(f"[viewer] got offer from {frm}")
                        offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["sdpType"])
                        await pc.setRemoteDescription(offer)
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        await ws.send(json.dumps({
                            "type": "answer",
                            "to": frm,
                            "sdp": pc.localDescription.sdp,
                            "sdpType": pc.localDescription.type,
                        }))
                    elif t == "answer" and role == "publisher":
                        print("[publisher] got answer")
                        answer = RTCSessionDescription(sdp=msg["sdp"], type=msg["sdpType"])
                        await pc.setRemoteDescription(answer)
                    elif t == "candidate":
                        try:
                            # cand = {
                            #     "candidate": msg["candidate"],
                            #     "sdpMid": msg.get("sdpMid"),
                            #     "sdpMLineIndex": msg.get("sdpMLineIndex"),
                            # }
                            cand = candidate_from_sdp(msg["candidate"].split("\n")[0])
                            await pc.addIceCandidate(cand)
                        except Exception as e:
                            print("Failed to add ICE:", e)
                    elif t == "peer-left":
                        print(f"[{role}] peer-left: {msg['peer']}")
                        lost_event.set()
                    elif t == "error":
                        print(f"[{role}] signaling error:", msg)
                        lost_event.set()
                    else:
                        pass
        except Exception as e:
            print(f"[{role}] Signaling loop error:", e)

        # đợi vài giây rồi thử reconnect lại signaling server
        await asyncio.sleep(timeout)
        
        
# ====== Gửi ping - chờ pong ======
async def heartbeat_task(channel, lost_event: asyncio.Event):
    ping_count = 0
    
    @channel.on("message")
    def on_message(message):
        # nonlocal last_pong
        nonlocal ping_count
        if message == "pong":
            # print("Got pong")
            ping_count = 0
            
    while True:
        if channel.readyState == "open":
            try:
                channel.send("ping")
                ping_count += 1
            except Exception:
                print("Heartbeat send failed")
                lost_event.set()
                return
        await asyncio.sleep(1)
        if ping_count == 5:
            print("ping count to 5 -> connection lost")
            lost_event.set()
            return

##### CÁC HÀM DÀNH CHO PUBLISHER #####

# ====== đọc từ uart rồi gửi đi ======
async def uart_reader(channel, ser: serial.Serial):
    data = b''
    try:
        while True:
            while ser.in_waiting > 0:
                byte = ser.read()
                data = data + byte

            if channel.readyState == "open":
                if len(data) != 0:
                    channel.send(data)
                    # print("[Publisher] Sent UART -> webrtc", data)
            else:
                print("[Publisher] No channel to send")
                ser.close()
                break
            data = b''
            asyncio.sleep(0.005)
    except asyncio.CancelledError:
        print("[Publisher] UART reader cancelled")
    finally:
        ser.close()

# ====== Gửi dữ liệu linh tinh mỗi vài giây ======
async def send_periodic(channel):
    while True:
        if channel.readyState == "open":
            msg = b"\x01\x08\xA5\x5B\x68"
            channel.send(msg)
        await asyncio.sleep(2)  # gửi mỗi vài giây

# ====== Track có khả năng reconnect ======
class ReconnectableTrack(VideoStreamTrack):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.player = None
        self._ensure_player()

    def _ensure_player(self):
        if self.player is None:
            print("Creating MediaPlayer...")
            self.player = MediaPlayer("rtp://0.0.0.0:40005",   # listen UDP 40005
                                      format="mpegts",         # vì trong RTP chứa TS
                                      options={
                                          "protocol_whitelist": "file,udp,rtp",  # cho phép udp/rtp
                                          "fflags": "nobuffer",
                                          "flags": "low_delay",
                                          "max_delay": "0",
                                          "reorder_queue_size": "0",
                                          "stimeout": "5000000",
                                      },
                                      decode=True)
            print("Done")

    async def recv(self):
        while True:
            try:
                self._ensure_player()
                frame = await self.player.video.recv()
                return frame
            except Exception as e:
                print(f"Camera lost: {e}, reconnecting...")
                if self.player:
                    await self.player.stop()
                self.player = None
                await asyncio.sleep(2)

# ====== Forward rtp (chưa hoạt động) ======        
class RtpForwardTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, port=40005, queue_size=100):
        super().__init__()
        self.port = port
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.transport_task = None
        
    async def start(self):
        """gọi sau khi có loop"""
        if self.transport_task is None:
            self.transport_task = asyncio.create_task(self._run())

    async def _run(self):
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.port))
        sock.setblocking(False)

        print(f"Listening for RTP on 0.0.0.0:{self.port}")
        while True:
            data, _ = await loop.sock_recvfrom(sock, 65535)
            # nếu queue đầy thì bỏ gói cũ
            if self.queue.full():
                try:
                    _ = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await self.queue.put(data)

    async def recv(self):
        # lấy raw rtp từ queue
        data = await self.queue.get()

        # tạo packet giả định là H264 (để aiortc hiểu)
        packet = av.packet.Packet(data)
        packet.stream = av.stream.Stream(codec_context=av.codec.CodecContext.create("h264", "r"))

        # trả packet thô (không decode)
        return packet
        
# ====== Track giả phát frame đen ======
class BlackFrameTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, width=640, height=480):
        super().__init__()  
        self.width = width
        self.height = height

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame(self.width, self.height, 'yuv420p')
        frame.pts = pts
        frame.time_base = time_base
        return frame

##### CÁC HÀM DÀNH CHO VIEWER #####

# ====== hiển thị bằng opencv ======
async def opencv_display(track):
    while True:
        try:
            frame = await track.recv()
            img = frame.to_ndarray(format="bgr24")
            cv2.imshow("Viewer", img)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        except Exception as e:
            print("Track error: ", e)
            break
    cv2.destroyAllWindows()

# ====== Gửi hết video packet qua UDP ======
async def send_video_packet_udp(track, udp_sock: socket.socket, GCS_IP: str, VIDEO_PORT: int):
    while True:
        frame = await track.recv()
        rtp_packet = await track.recv_rtp_packet()
        # print("_data: ", rtp_packet._data[:80])
        # print("payload: ", rtp_packet.payload[:80])
        # rtp_packet.payload = rtp_packet._data
        try:
            udp_sock.sendto(rtp_packet._data, (GCS_IP, VIDEO_PORT))
        except Exception as e:
            print("UDP send error: ", e)
            
# ====== nhận lệnh điều khiển từ udp rồi gửi đi ======
async def send_command_from_gcs(channel, lost_event: asyncio.Event, udp_sock: socket.socket, GCS_IP: str, TELEM_PORT: int):
    udp_sock.setblocking(False)

    # gửi lần đầu
    udp_sock.sendto(b"Hello GCS", (GCS_IP, TELEM_PORT))
    last_recv_time = time.time()

    while True:
        if lost_event.is_set():
            break
        try:
            # kiểm tra có dữ liệu trong buffer không
            rlist, _, _ = select.select([udp_sock], [], [], 0)
            if rlist:
                packet, addr = udp_sock.recvfrom(65535)
                last_recv_time = time.time()   # cập nhật thời gian nhận

                if channel.readyState == "open":
                    channel.send(packet)
                    # print("[Viewer] GCS Sent command ->", packet)
                else:
                    print("[Viewer] No channel to send") 
            else:
                await asyncio.sleep(0.005)

            # 🔥 Nếu 1 giây chưa nhận được packet nào -> gửi lại Hello
            if time.time() - last_recv_time >= 2.0:
                udp_sock.sendto(b"Hello GCS", (GCS_IP, TELEM_PORT))
                print("[UDP] Resent Hello GCS")
                last_recv_time = time.time()

            await asyncio.sleep(0.1)

        except Exception as e:
            print("[UDP exception]:", e) 
            await asyncio.sleep(1)
            
async def opencv_to_gstreamer(track):
    gst_cmd = (
        "gst-launch-1.0 -v fdsrc ! rawvideoparse format=bgr width=1920 height=1080 framerate=30/1 "
        "! videoconvert ! x264enc tune=zerolatency  speed-preset=ultrafast "
        "! rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port=5002" 
    )
    gst_proc = subprocess.Popen(gst_cmd, shell=True, stdin=subprocess.PIPE)
    while True:
        try:
            frame = await track.recv()
            img = frame.to_ndarray(format="bgr24")
            # resized = cv2.resize(img, (640, 480))
            # ghi ra pipe cho gstreamer
            gst_proc.stdin.write(img.tobytes())
            # cv2.imshow("Viewer", img) 
            # if cv2.waitKey(1) & 0xFF == ord("q"):
            #     break
        except Exception as e:
            print(f"[opencv_display] Exception: {e}")

    cv2.destroyAllWindows()
