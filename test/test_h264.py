import time
import cv2
import numpy as np
import asyncio
import av   # PyAV
from io import BytesIO

# ========== CONFIG ==========
FPS = 25
WIDTH = 1920
HEIGHT = 1080

# khởi ffmpeg: đọc raw BGR từ stdin, xuất H264 (annexb) ra stdout
ffmpeg_cmd = [
    "C:/ffmpeg/bin/ffmpeg.exe",
    "-y",
    "-f", "rawvideo",
    "-pix_fmt", "bgr24",
    "-s", f"{WIDTH}x{HEIGHT}",
    "-r", str(FPS),
    "-i", "-",                     # stdin
    "-an",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-f", "h264",                  # annex-b raw h264 stream
    "-"                            # stdout
]

async def main():
    # đọc từ camera, gửi vào ffmpeg stdin
    cap = cv2.VideoCapture('test.mp4')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)

    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    
    # buffer đọc stdout h264
    h264_buffer = b""

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # print(type(frame))
            
            frame = cv2.resize(frame, (WIDTH, HEIGHT))
            # write raw frame to ffmpeg stdin
            proc.stdin.write(frame.tobytes())
            #print("write frame")
            
            # đọc tất cả bytes sẵn có từ ffmpeg stdout (non-blocking approach: read some)
            # để đơn giản ở đây ta đọc một chunk (tùy môi trường, có thể cần select)
            time.sleep(0.005)  # cho ffmpeg chút thời gian encode
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                h264_buffer += chunk
                if(len(chunk) < 4096):
                    break

            try:
                # use PyAV to decode the annex b h264 bytes
                container = av.open(BytesIO(h264_buffer), format='h264')
                for packet in container.demux():
                    #print(format(packet))q
                    for frame in packet.decode():
                        #print(frame)
                        img = frame.to_ndarray(format='bgr24')
                        cv2.imshow("H264 RTP Receiver", img)
                        #print("showed")
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            cv2.destroyAllWindows()
                            raise SystemExit(0)
            except Exception as e:
                # decoding may fail if stream incomplete; ignore and retry later
                pass
            # discard parsed prefix to avoid memory growth
            # keep trailing bytes from last start (unparsed)
            idx = h264_buffer.find(b'\x00\x00\x00\x01')

            #print(f"first start {first_start}")
            if idx == -1:
                h264_buffer = b''
                #print("reset")
            else:
                # keep from first_start (the earliest incomplete nal) to end
                h264_buffer = h264_buffer[idx:]
                #print("not reset")
                
            # pacing
            time.sleep(1.0 / FPS)
        
    finally:
        cap.release()
        proc.stdin.close()
        #proc.stdout.close()
        proc.kill()

asyncio.run(main())
