# receiver_h264_rtp.py
import socket
import struct
import threading
import time
import av   # PyAV
import numpy as np
import cv2
from io import BytesIO

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 5055

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((LISTEN_IP, LISTEN_PORT))

# buffer per timestamp: { timestamp: { 'nals': {offset_key: bytes}, 'assembled_bytes': None, 'last_seq': None } }
# For RFC6184 we will reassemble per timestamp using order S/E flags.
frames = {}
lock = threading.Lock()

def rtp_unpack_header(pkt):
    if len(pkt) < 12:
        return None
    v_p_x_cc, m_pt, seq, timestamp, ssrc = struct.unpack("!BBHII", pkt[:12])
    payload = pkt[12:]
    marker = (m_pt & 0x80) != 0
    pt = m_pt & 0x7F
    return {
        'version': (v_p_x_cc >> 6) & 0x03,
        'marker': marker,
        'pt': pt,
        'seq': seq,
        'timestamp': timestamp,
        'ssrc': ssrc,
        'payload': payload
    }

def handle_packet(rtp):
    payload = rtp['payload']
    if len(payload) < 1:
        return
    nal_first = payload[0]
    nal_type = nal_first & 0x1F

    # Single NAL unit (1..23) -> payload is the whole NAL
    if 1 <= nal_type <= 23:
        # Prepend Annex-B start code
        nal_bytes = b'\x00\x00\x00\x01' + payload
        with lock:
            ts = rtp['timestamp']
            key = ts
            frames.setdefault(key, {'nals': [], 'last_recv_time': time.time()})
            frames[key]['nals'].append(nal_bytes)
    elif nal_type == 28:
        # FU-A
        if len(payload) < 2:
            return
        fu_indicator = payload[0]
        fu_header = payload[1]
        S = (fu_header >> 7) & 0x1
        E = (fu_header >> 6) & 0x1
        orig_type = fu_header & 0x1F
        # reconstruct original NAL header: F | NRI | type
        F = (fu_indicator >> 7) & 0x1
        NRI = (fu_indicator >> 5) & 0x3
        orig_nal_header = bytes([ (F << 7) | (NRI << 5) | orig_type ])
        fragment_data = payload[2:]
        ts = rtp['timestamp']
        with lock:
            key = ts
            frames.setdefault(key, {'fragments': [], 'last_recv_time': time.time(), 'assembled': False})
            entry = frames[key]
            # store fragments in order by seq (we can't assume arrival order but for simplicity we append)
            entry.setdefault('packets', [])
            entry['packets'].append({'seq': rtp['seq'], 'S': S, 'E': E, 'data': fragment_data, 'orig_header': orig_nal_header})
            entry['last_recv_time'] = time.time()

            # if E==1 -> last fragment of that NAL. Let's check if we have S..E -> assemble
            if E == 1:
                # attempt to find the contiguous sequence containing S..E by sorting by seq
                pkts = sorted(entry['packets'], key=lambda x: x['seq'])
                # we need to group packets between S and E for each FU-A NAL
                # a robust implementation would track sequences; here we try simple greedy grouping
                assembled_nal = None
                collecting = False
                collected = b''
                for p in pkts:
                    if p['S'] == 1:
                        collecting = True
                        # start with original NAL header
                        collected = p['orig_header'] + p['data']
                        if p['E'] == 1:
                            # single-fragment FU (S and E)
                            assembled_nal = b'\x00\x00\x00\x01' + collected
                            break
                    elif collecting:
                        collected += p['data']
                        if p['E'] == 1:
                            assembled_nal = b'\x00\x00\x00\x01' + collected
                            break
                if assembled_nal is not None:
                    entry.setdefault('nals', [])
                    entry['nals'].append(assembled_nal)
                    # remove packets used (simple approach: clear packets)
                    entry['packets'] = []
    else:
        # other NAL types (STAP-A, etc) not handled here
        pass

def receiver_loop():
    while True:
        pkt, addr = sock.recvfrom(65536)
        r = rtp_unpack_header(pkt)
        if r is None:
            continue
        handle_packet(r)

# spawn receiver thread
t = threading.Thread(target=receiver_loop, daemon=True)
t.start()

# Decoding loop: periodically check frames dict for assembled NALs and decode using PyAV
while True:
    time.sleep(0.03)
    now = time.time()
    to_delete = []
    with lock:
        for ts, entry in list(frames.items()):
            # if we have 'nals' and they were updated recently, decode them as a frame
            if 'nals' in entry and len(entry['nals']) > 0:
                # combine nals into a single annexb stream
                stream = b''.join(entry['nals'])
                try:
                    # use PyAV to decode the annex b h264 bytes
                    container = av.open(BytesIO(stream), format='h264')
                    for packet in container.demux():
                        for frame in packet.decode():
                            img = frame.to_ndarray(format='bgr24')
                            cv2.imshow("H264 RTP Receiver", img)
                            if cv2.waitKey(1) & 0xFF == ord('q'):
                                sock.close()
                                cv2.destroyAllWindows()
                                raise SystemExit(0)
                except Exception as e:
                    # decoding may fail if stream incomplete; ignore and retry later
                    pass
                to_delete.append(ts)
            else:
                # if entry too old, drop
                if now - entry.get('last_recv_time', now) > 2.0:
                    to_delete.append(ts)
        # cleanup
        for ts in to_delete:
            frames.pop(ts, None)
