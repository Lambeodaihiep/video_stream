import argparse
import socket

import asyncio
import json
import cv2
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRecorder
from aiortc.rtp import RtpPacket

# ====== TURN SERVER ======
TURN_HOST     = "192.168.0.117:3478"       # "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"

# ====== GCS PORT ======
VIDEO_PORT = 5001
TELEMETRY_PORT = 14450
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

# ====== Gửi dữ liệu mỗi vài giây ======
async def send_periodic(channel):
    counter = 0
    while True:
        if channel.readyState == "open":
            msg = f"[Viewer] Hello {counter}"
            channel.send(msg)
            print("Sent:", msg)
            counter += 1
        await asyncio.sleep(3)  # gửi mỗi 3 giây

# ====== run viewer ======
async def run_viewer(host: str, port: int, GCS_IP: str, timeout: int):
    pc = None
    writer = None
    chat_channel = None
    recorder = None
    try:
        # tạo kết nối tới server webrtc
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout
        )
        await send_json(writer, {"role": "viewer"})

        ice_servers = [
            #RTCIceServer(urls=["stun.l.google.com:19302"])
            RTCIceServer(urls=[f"stun:{TURN_HOST}"]),  # STUN free
            RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS)  # TURN riêng
        ]
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
            nonlocal chat_channel
            chat_channel = channel
            print("[Viewer] Data channel received:", channel.label)

            # test gửi tin nhắn sau khi mở channel
            # chỗ này phải tự mở data channel riêng thì hàm này mới chạy
            @channel.on("open")
            def on_open():
                channel.send("[Viewer] Chat channel opened, you can type messages...")
                # asyncio.ensure_future(send_periodic(channel))
                
            # nhận được tin nhắn thì in ra và gửi qua udp
            @channel.on("message")
            def on_message(msg):
                #print("[Viewer] Received:", msg)
                try:
                    if not isinstance(msg, bytes):
                        data = msg.tobytes()
                        udp_sock.sendto(data, (GCS_IP, TELEMETRY_PORT))
                    else:
                        udp_sock.sendto(msg, (GCS_IP, TELEMETRY_PORT))
                except Exception as e:
                    print("UDP send error: ", e)

        # nhận được track video thì hiển thị hoặc là gửi cho thiết bị khác qua udp
        @pc.on("track")
        def on_track(track):
            print("[Viewer] Track received, track kind: ", track.kind)
            asyncio.ensure_future(opencv_display(track))          
            # asyncio.ensure_future(send_rtp_packet_udp(track, udp_sock, GCS_IP))

            ##### stream thẳng qua udp
            # nonlocal recorder
            # if track.kind == "video":
            #     # Create ffmpeg recorder that streams raw H264 (or re-packetizes) to UDP dest
            #     dst = f"udp://{args.GCS_IP}:{VIDEO_PORT}"
            #     print(f"[Viewer] Creating recorder -> {dst}")

            #     # format="h264" requests FFmpeg to output raw H.264 stream.
            #     # Note: if the incoming codec is H.264, FFmpeg will usually passthrough.
            #     recorder = MediaRecorder(
            #         dst,
            #         format="h264",
            #         options={
            #             "vcodec": "copy",  # copy codec (no re-encoding)
            #             "fflags": "nobuffer",
            #             "flags": "low_delay",
            #             "max_delay": "0",
            #             "flush_packets": "1",
            #             "probesize": "32",
            #             "analyzeduration": "0",
            #             "pkt_size": "1200",
            #             "tune": "zerolatency"
            #         }
            #     )
            #     recorder.addTrack(track)
            #     # start recorder coroutine (runs ffmpeg process)
            #     asyncio.ensure_future(recorder.start())
            #     print("[Viewer] Recorder started, forwarding to UDP")
                    
        # nhận offer từ server
        msg = await recv_json(reader, timeout=10)
        if not msg:
            print("[Viewer] Timeout/no data")
            return "retry"
        if "event" in msg :
            if msg["event"] in ["no_publisher", "publisher_disconnected"]:
                print(f"Event {msg['event']}, retry in {RETRY_TIME} seconds ...")
                udp_sock.close()
                writer.close()
                await writer.wait_closed()
                return "retry"
        
        offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
        await pc.setRemoteDescription(offer)

        # tạo answer gửi lại server
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await send_json(writer, {"type": answer.type, "sdp": answer.sdp})

        # vòng lặp nhập console để gửi
        # loop = asyncio.get_running_loop()
        # giữ kết nối

        while True:
            # ====== nhập vào từ màn hình để gửi đi ======
            # msg = await loop.run_in_executor(None, input, "You> ")
            # if chat_channel and chat_channel.readyState == "open":
            #    chat_channel.send(msg)
            # else:
            #    print("[Viewer] Chat channel not ready yet")

            # ====== nhận tin nhắn từ server ======
            msg = await recv_json(reader)
            if msg is None:
                raise ConnectionError("Server lost")
            if "event" in msg and msg["event"] == "publisher_disconnected":
                print(f"Publisher disconnected, retrying in {RETRY_TIME} seconds ...")
                return "retry"
            await asyncio.sleep(0)

    except asyncio.TimeoutError:
        print(f"Timeout {timeout}s, could not connect to {host}:{port}")
        return "retry"

    except Exception as e:
        print("[Viewer] Error:", e)
        return "retry"
    
    finally:
        if recorder:
            try:
                await recorder.stop()
            except Exception as e:
                print("Error stopping recorder:", e)
        if pc:
            await pc.close()
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
        "--GCS_IP", default="127.0.0.1", help="GCS IP address to send data"
    )
    parser.add_argument(
        "--timeout", type=int, default=3, help="Server connecting timeout (default: 3s)"
    )
    args = parser.parse_args()

    while True:
        result = await run_viewer(args.host, args.port, args.GCS_IP, args.timeout)
        if result == "retry":
            await asyncio.sleep(RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break

if __name__ == "__main__":
    asyncio.run(main())
