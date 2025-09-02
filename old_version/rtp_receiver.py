import socket
import struct
import cv2
import numpy as np
import time

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 5055

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((LISTEN_IP, LISTEN_PORT))

frame_buffer = {}
current_timestamp = None
expected_size = None
prev_time_frame = 0
curr_time_frame = 0
font = cv2.FONT_HERSHEY_SIMPLEX 

while True:
    packet, _ = sock.recvfrom(65535)
    if len(packet) < 18:  # RTP (12) + JPEG header (6)
        continue

    # RTP header
    v_p_x_cc, m_pt, seq, timestamp, ssrc = struct.unpack("!BBHII", packet[:12])
    m_bit = (m_pt & 0x80) != 0
    payload_type = m_pt & 0x7F

    # JPEG header
    tspec, off1, off2, off3, jpeg_type, q = struct.unpack("!B3B2B", packet[12:18])
    offset = (off1 << 16) | (off2 << 8) | off3

    jpeg_payload = packet[18:]

    # Nếu là frame mới → reset buffer
    if timestamp != current_timestamp:
        frame_buffer = {}
        current_timestamp = timestamp
        expected_size = None

    frame_buffer[offset] = jpeg_payload

    # Nếu đây là mảnh cuối → tính tổng kích thước mong đợi
    if m_bit:
        expected_size = offset + len(jpeg_payload)

    # Khi đã có expected_size → kiểm tra đủ mảnh chưa
    if expected_size is not None:
        received_size = sum(len(v) for v in frame_buffer.values())
        if received_size == expected_size:
            # Ghép mảnh
            ordered_data = b''.join(frame_buffer[k] for k in sorted(frame_buffer.keys()))
            np_arr = np.frombuffer(ordered_data, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            curr_time_frame = time.time() 
            fps = 1/(curr_time_frame - prev_time_frame)
            prev_time_frame = curr_time_frame 
            if frame is not None:
                cv2.putText(frame, 'fps: ' + str(round(fps, 2)), (7, 70), font, 2, (100, 255, 0), 3, cv2.LINE_AA) 
                cv2.imshow("RTP Receiver", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            frame_buffer = {}
            expected_size = None

sock.close()
cv2.destroyAllWindows()
