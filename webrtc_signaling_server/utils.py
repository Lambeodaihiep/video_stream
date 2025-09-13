import websockets, json, asyncio, serial, cv2, socket
from aiortc.sdp import candidate_from_sdp
from aiortc import RTCPeerConnection, RTCSessionDescription


###### CÁC HÀM DÙNG CHUNG #####

# ====== Task giữ kết nối signaling server ======
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
    Không đóng peer connection, trừ khi lost_event set()
    """
    #while not lost_event.is_set():
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
        
        
# ====== Gửi ping - chờ pong ======
async def heartbeat_task(channel, lost_event):
    last_pong = asyncio.get_event_loop().time()
    
    @channel.on("message")
    def on_message(message):
        nonlocal last_pong
        if message == "pong":
            print("Got pong")
            last_pong = asyncio.get_event_loop().time()
            
    while True:
        if channel.readyState == "open":
            try:
                channel.send("ping")
            except Exception:
                print("Heartbeat send failed")
                lost_event.set()
                return
        await asyncio.sleep(5)
        if asyncio.get_event_loop().time() - last_pong > 5:
            print("No pong in 5s -> connection lost")
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
            

