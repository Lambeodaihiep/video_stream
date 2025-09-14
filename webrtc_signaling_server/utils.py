import websockets, json, asyncio, serial, cv2, socket, subprocess
from aiortc.sdp import candidate_from_sdp
from aiortc import RTCPeerConnection, RTCSessionDescription


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
                    elif t == "peer-joined":
                        peer = msg["peer"]
                        print(f"[{role}] Peer joined: {peer}")
                        if role == "publisher" and pc.signalingState == "stable":
                            # khi có viewer mới -> gửi offer
                            offer = await pc.createOffer()
                            await pc.setLocalDescription(offer)
                            await ws.send(json.dumps({
                                "type": "offer",
                                "to": peer,
                                "sdp": pc.localDescription.sdp,
                                "sdpType": pc.localDescription.type,
                            }))
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
                            cand = {
                                "candidate": msg["candidate"],
                                "sdpMid": msg.get("sdpMid"),
                                "sdpMLineIndex": msg.get("sdpMLineIndex"),
                            }
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
async def heartbeat_task(channel, lost_event):
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
async def uart_reader(channel, COM_port: str, baudrate: int):
    ser = serial.Serial(COM_port, baudrate=baudrate, timeout=1)
    ser.reset_input_buffer() 
    ser.reset_output_buffer() 
    print(f"[Publisher] UART opened at {COM_port} {baudrate}")
    loop = asyncio.get_event_loop()
    data = b''
    try:
        while True:
            if ser.in_waiting > 0:
                byte = await loop.run_in_executor(None, ser.read)
                data = data + byte
            else:
                if channel.readyState == "open":
                    if len(data) != 0:
                        channel.send(data)
                        print("[Publisher] Sent UART -> webrtc", data)
                else:
                    print("[Publisher] No channel to send")
                    ser.close()
                    break
                data = b''
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
async def send_command_from_gcs(channel, udp_sock: socket.socket):
    udp_sock.setblocking(False)
    loop = asyncio.get_event_loop()

    while True:
        try:
            packet, _ = await asyncio.wait_for(
                loop.sock_recvfrom(udp_sock, 65535),
                timeout=1.0
            )
            if channel.readyState == "open":
                channel.send(packet)
                print("[Viewer] Sent command ->", packet)
            else:
                print("[Viewer] No channel to send")
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            pass
            
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
