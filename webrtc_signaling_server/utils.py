import websockets, json, asyncio, serial, cv2, socket
from aiortc.sdp import candidate_from_sdp
from aiortc import RTCPeerConnection, RTCSessionDescription


###### CÁC HÀM DÙNG CHUNG #####

# ====== Task giữ kết nối signaling và peer connection độc lập ======
async def signaling_loop(pc: RTCPeerConnection,
                         lost_event: asyncio.Event,
                         on_icecandidate,
                         role: str,
                         timeout: int,
                         signaling_server: str
    ):
    """
    Loop để duy trì kết nối với signaling server.
    Nếu WS rớt thì tự động reconnect.
    Không đóng PC, trừ khi lost_event set() (peer thực sự mất).
    """
    while not lost_event.is_set():
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

                    if lost_event.is_set():
                        print("Peer lost -> exit signaling loop")
                        return

        except Exception as e:
            print("Signaling connection error:", e)

        # đợi giây rồi thử reconnect lại WS
        await asyncio.sleep(timeout)

##### CÁC HÀM DÀNH CHO PUBLISHER #####

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
            # msg = b"\x01\x08\xA5\x5B\x68"
            msg = "ping"
            channel.send(msg)
        await asyncio.sleep(2)  # gửi mỗi vài giây

##### CÁC HÀM DÀNH CHO VIEWER #####

# ====== hiển thị bằng opencv ======
async def opencv_display(track):
    while True:
        frame = await track.recv()
        img = frame.to_ndarray(format="bgr24")
        cv2.imshow("Viewer", img)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()

# ====== Gửi hết video packet qua UDP ======
async def send_video_packet_udp(track, udp_sock: socket.socket, GCS_IP: str, VIDEO_PORT: int):
    while True:
        rtp_packet = await track.recv_rtp_packet()
        # print(len(rtp_packet._data))
        try:
            udp_sock.sendto(rtp_packet._data, (GCS_IP, VIDEO_PORT))
        except Exception as e:
            print("UDP send error: ", e)

