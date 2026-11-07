"""Fake iPad panel: TCP server that streams touch frames, for testing bridge.py
without the device or a build.

    python sim_app.py                 # 1P, port 24870
    python sim_app.py --player 2      # 2P, port 24880
    python bridge.py --serial none
"""

import math
import socket
import struct
import sys
import threading
import time

from bridge import (
    FRAME_BUTTONS, FRAME_CONTROL, FRAME_LED, FRAME_MAGIC, FRAME_PING,
    FRAME_SYSTEM, FRAME_TOUCH, LED_MAGIC, LED_PORT, ZONES, log,
)


def frame(frame_type, payload):
    return FRAME_MAGIC + bytes([1, frame_type]) + struct.pack("<H", len(payload)) + payload


def send_mask(conn, mask):
    conn.sendall(frame(FRAME_TOUCH, mask.to_bytes(5, "little")))


def send_buttons(conn, mask):
    conn.sendall(frame(FRAME_BUTTONS, bytes([mask])))


def send_system(conn, key_id):
    conn.sendall(frame(FRAME_SYSTEM, bytes([key_id])))


def send_control(conn, cmd, value):
    conn.sendall(frame(FRAME_CONTROL, bytes([cmd, value])))


def read_frames(conn):
    """Logs frames coming back from the bridge (LED relay)."""
    buf = bytearray()
    while True:
        try:
            data = conn.recv(4096)
        except OSError:
            return
        if not data:
            return
        buf += data
        while len(buf) >= 8:
            if bytes(buf[:4]) != FRAME_MAGIC:
                del buf[:1]
                continue
            length = struct.unpack_from("<H", buf, 6)[0]
            if len(buf) < 8 + length:
                break
            frame_type = buf[5]
            payload = bytes(buf[8:8 + length])
            del buf[:8 + length]
            if frame_type == FRAME_LED and len(payload) >= 28:
                colors = ["%02x%02x%02x" % tuple(payload[1 + i * 3:4 + i * 3]) for i in range(8)]
                log(f"LED valid={payload[0] & 1} {' '.join(colors)} "
                    f"b/c/s={payload[25]}/{payload[26]}/{payload[27]}")


def led_demo(player=1):
    """Sends fake mod datagrams so the app can be tested without the game."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    phase = 0.0
    while True:
        colors = []
        for index in range(8):
            angle = phase + index * 0.7
            colors.extend([
                int((math.sin(angle) + 1) * 127.5),
                int((math.sin(angle + 2.1) + 1) * 127.5),
                int((math.sin(angle + 4.2) + 1) * 127.5),
            ])
        payload = bytes([player, 1] + colors + [200, 120, 60])
        sock.sendto(LED_MAGIC + payload, ("127.0.0.1", LED_PORT))
        phase += 0.15
        time.sleep(1 / 30)


def session(conn, player=1):
    log(f"bridge 已连接 ({player}P)")
    send_control(conn, 2, player)
    threading.Thread(target=read_frames, args=(conn,), daemon=True).start()
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
    arguments = list(sys.argv[1:])
    player = 1
    if "--player" in arguments:
        index = arguments.index("--player")
        if index + 1 < len(arguments) and arguments[index + 1] == "2":
            player = 2
        del arguments[index:index + 2]
    positional = [arg for arg in arguments if not arg.startswith("--")]
    port = int(positional[0]) if positional else (24880 if player == 2 else 24870)
    if "--led" in arguments:
        threading.Thread(target=led_demo, args=(player,), daemon=True).start()
        log(f"模拟 {player}P 灯光每 33ms 发往 127.0.0.1:{LED_PORT}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    log(f"模拟 {player}P iPad 监听 {port}")
    while True:
        conn, address = server.accept()
        log(f"来自 {address}")
        threading.Thread(target=session, args=(conn, player), daemon=True).start()


if __name__ == "__main__":
    main()
