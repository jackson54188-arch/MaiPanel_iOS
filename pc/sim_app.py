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

from bridge import (
    FRAME_BUTTONS, FRAME_MAGIC, FRAME_PING, FRAME_SYSTEM, FRAME_TOUCH, ZONES, log,
)


def frame(frame_type, payload):
    return FRAME_MAGIC + bytes([1, frame_type]) + struct.pack("<H", len(payload)) + payload


def send_mask(conn, mask):
    conn.sendall(frame(FRAME_TOUCH, mask.to_bytes(5, "little")))


def send_buttons(conn, mask):
    conn.sendall(frame(FRAME_BUTTONS, bytes([mask])))


def send_system(conn, key_id):
    conn.sendall(frame(FRAME_SYSTEM, bytes([key_id])))


def session(conn):
    log("bridge 已连接")
    try:
        for index in range(len(ZONES)):
            send_mask(conn, 1 << index)
            log(f"TX {ZONES[index]}")
            time.sleep(0.3)
        send_mask(conn, 0)
        log("TX 全部松开")
        for index in range(8):
            send_buttons(conn, 1 << index)
            log(f"BTN {index + 1}")
            time.sleep(0.3)
        send_buttons(conn, 0)
        log("BTN 全部松开")
        for key_id, name in enumerate(("TEST", "SERVICE", "投币", "三角"), start=1):
            send_system(conn, key_id)
            log(f"SYS {name}")
            time.sleep(0.4)
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
