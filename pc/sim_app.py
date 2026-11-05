"""Fake iPad panel: TCP server that streams touch frames, for testing bridge.py
without the device or a build.

    python sim_app.py
    python bridge.py --serial none
"""

import socket
import struct
import sys
import threading
import time

from bridge import FRAME_MAGIC, FRAME_PING, FRAME_TOUCH, ZONES, log


def frame(frame_type, payload):
    return FRAME_MAGIC + bytes([1, frame_type]) + struct.pack("<H", len(payload)) + payload


def send_mask(conn, mask):
    conn.sendall(frame(FRAME_TOUCH, mask.to_bytes(5, "little")))


def session(conn):
    log("bridge 已连接")
    try:
        for index in range(len(ZONES)):
            send_mask(conn, 1 << index)
            log(f"TX {ZONES[index]}")
            time.sleep(0.3)
        send_mask(conn, 0)
        log("TX 全部松开")
        while True:
            time.sleep(1)
    except OSError:
        pass
    finally:
        log("bridge 断开")
        conn.close()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 24870
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    log(f"模拟 iPad 监听 {port}")
    while True:
        conn, address = server.accept()
        log(f"来自 {address}")
        threading.Thread(target=session, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
