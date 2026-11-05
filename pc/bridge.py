"""Bridge between the iPad touch panel app and the maimai touch panel serial port.

    iPad app (TCP server, port 24870)
        <-- usbmux over USB -->  pymobiledevice3 usbmux forward 24870 24870
    this script (TCP client 127.0.0.1:24870 + serial writer)
        <-- virtual COM pair (com0com) -->  Sinmai.exe on COM3

Touch frames coming from the iPad are converted to the panel serial protocol:
0x28 <7 bytes, 5 zones per byte> 0x29, and the game's handshake is answered
so the panel is accepted when the game boots.

Run:
    python bridge.py --serial COM33
    python bridge.py --serial COM33 --usb        (also starts the usbmux forward)
"""

import argparse
import socket
import struct
import subprocess
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial 缺失, 请先 pip install pyserial")

ZONES = (
    [f"A{i}" for i in range(1, 9)]
    + [f"B{i}" for i in range(1, 9)]
    + ["C1", "C2"]
    + [f"D{i}" for i in range(1, 9)]
    + [f"E{i}" for i in range(1, 9)]
)

FRAME_MAGIC = b"MPNL"
FRAME_TOUCH = 0x01
FRAME_PING = 0x02
FRAME_PONG = 0x03


def build_packet(mask):
    out = bytearray([0x28])
    for byte_index in range(7):
        value = 0
        for bit in range(5):
            zone = byte_index * 5 + bit
            if zone < len(ZONES) and (mask >> zone) & 1:
                value |= 1 << bit
        out.append(value)
    out.append(0x29)
    return bytes(out)


def zone_names(mask):
    return [ZONES[i] for i in range(len(ZONES)) if (mask >> i) & 1]


def log(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class DrySerial:
    """Stand-in for the virtual COM pair: prints packets instead of writing them."""

    def __init__(self):
        self.is_open = True

    @property
    def in_waiting(self):
        return 0

    def read(self, size):
        return b""

    def write(self, data):
        log("SERIAL " + data.hex(" "))

    def close(self):
        self.is_open = False


def open_serial(port, baud):
    if port.lower() == "none":
        log("未指定串口, 只打印数据包 (dry run)")
        return DrySerial()
    return serial.Serial(port, baud, timeout=0.05)


class SerialLink:
    """Owns the panel side of the virtual COM pair."""

    def __init__(self, port, baud):
        self.ser = open_serial(port, baud)
        self.lock = threading.Lock()
        self.connected = isinstance(self.ser, DrySerial)
        self.mask = 0
        self.running = True

    def start(self):
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()

    def _reader(self):
        buf = bytearray()
        while self.running:
            try:
                waiting = self.ser.in_waiting
                if not waiting:
                    time.sleep(0.002)
                    continue
                buf += self.ser.read(waiting)
            except Exception as error:  # serial disappears when the game exits
                log(f"串口读取失败: {error}")
                time.sleep(0.5)
                continue

            while len(buf) >= 6:
                chunk = buf[:6]
                marker = chunk[3]
                if marker in (ord("r"), ord("k")):
                    reply = bytes([0x28, chunk[1], chunk[2], chunk[3], chunk[4], 0x29])
                    self._raw_write(reply)
                    log("握手 " + reply.hex(" "))
                    del buf[:6]
                elif marker == ord("A"):
                    if not self.connected:
                        log("游戏已接受触摸板")
                    self.connected = True
                    del buf[:6]
                elif marker in (ord("L"), ord("E")):
                    if self.connected:
                        log("触摸板已断开")
                    self.connected = False
                    del buf[:6]
                else:
                    del buf[:1]

    def _writer(self):
        last = None
        while self.running:
            packet = build_packet(self.mask)
            if self.connected and packet != last:
                self._raw_write(packet)
                last = packet
            time.sleep(0.004)

    def _raw_write(self, data):
        with self.lock:
            try:
                self.ser.write(data)
            except Exception as error:
                log(f"串口写入失败: {error}")

    def set_mask(self, mask):
        self.mask = mask

    def close(self):
        self.running = False
        try:
            self.ser.close()
        except Exception:
            pass


class AppLink:
    """TCP client to the forwarded iPad port."""

    def __init__(self, host, port, on_mask):
        self.host = host
        self.port = port
        self.on_mask = on_mask
        self.running = True
        self.sock = None

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2)
            except OSError:
                time.sleep(1)
                continue
            self.sock = sock
            sock.settimeout(None)
            log(f"已连接 iPad {self.host}:{self.port}")
            try:
                self._read_frames(sock)
            except OSError:
                pass
            finally:
                log("iPad 连接断开")
                self.on_mask(0)
                try:
                    sock.close()
                except OSError:
                    pass
                self.sock = None

    def _read_frames(self, sock):
        buf = bytearray()
        while self.running:
            chunk = sock.recv(4096)
            if not chunk:
                return
            buf += chunk
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
                if frame_type == FRAME_TOUCH and len(payload) >= 5:
                    self.on_mask(int.from_bytes(payload[:5], "little"))
                elif frame_type == FRAME_PING:
                    sock.sendall(FRAME_MAGIC + bytes([1, FRAME_PONG, 0, 0]))


def start_forward(local_port, device_port):
    command = [
        sys.executable, "-m", "pymobiledevice3", "usbmux",
        "forward", str(local_port), str(device_port),
    ]
    log("启动 usbmux 转发: " + " ".join(command))
    return subprocess.Popen(command)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="COM33")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=24870)
    parser.add_argument("--usb", action="store_true", help="顺带启动 pymobiledevice3 usbmux forward")
    args = parser.parse_args()

    forward = start_forward(args.port, args.port) if args.usb else None

    serial_link = SerialLink(args.serial, args.baud)
    app_link = AppLink(args.host, args.port, serial_link.set_mask)
    serial_link.start()
    app_link.start()
    log(f"串口 {args.serial}@{args.baud} 就绪, 等待 iPad {args.host}:{args.port}")

    try:
        while True:
            try:
                line = input().strip().lower()
            except EOFError:  # no console attached (started detached / in background)
                time.sleep(1)
                continue
            if line in ("q", "quit", "exit"):
                break
            if line == "z":
                serial_link.set_mask(0)
                log("清空触摸")
            elif line == "s":
                log(f"串口连接: {serial_link.connected}  mask={serial_link.mask:#x} "
                    f"{' '.join(zone_names(serial_link.mask))}")
    except KeyboardInterrupt:
        pass
    finally:
        app_link.running = False
        serial_link.close()
        if forward:
            forward.terminate()
        log("已退出")


if __name__ == "__main__":
    main()
