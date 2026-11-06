"""Bridge between the iPad touch panel app and the maimai touch panel serial port.

    iPad app (TCP server, port 24870)
        <-- usbmux over USB -->  in-process forward (pc/forward.py)
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
import json
import socket
import struct
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
FRAME_BUTTONS = 0x04
FRAME_SYSTEM = 0x05
FRAME_LED = 0x06

# pc/ledmod/MaiPanelLed.cs sends 33-byte datagrams; forward the 28 bytes the
# iPad needs (flags, 8 x RGB, body/circle/side brightness).
LED_MAGIC = b"MPL1"
LED_PORT = 24872
LED_UDP_SIZE = 33
LED_PAYLOAD_SIZE = 28

# Payload id from the iPad's system-key frame -> SYSTEM_VKS name.
SYSTEM_FRAME_KEYS = {1: "test", 2: "service", 3: "coin", 4: "select", 5: "card"}

# Outer buttons 1..8 -> keyboard E D C X Z A Q W (virtual key codes).
BUTTON_VKS = (0x45, 0x44, 0x43, 0x58, 0x5A, 0x41, 0x51, 0x57)

# Cabinet operator keys, tapped from the monitor window. These match
# segatools.ini [io4] (test/service/coin) and AquaMai Select_1P.
SYSTEM_VKS = {"test": 0x31, "service": 0x38, "coin": 0x33, "select": 0x32,
              "card": 0x0D}
SYSTEM_LABELS = {"test": "TEST", "service": "SERVICE", "coin": "投币", "select": "三角",
                 "card": "刷卡"}

# aimeio presents its virtual card only while Enter is held down, so the card
# key needs a much longer press than the other keys.
SYSTEM_HOLDS = {"card": 1.2}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _INPUT_KEYBOARD = 1
    _KEYEVENTF_KEYUP = 0x0002


class KeyInjector:
    """Turns the iPad outer-button bitmask into real keyboard presses."""

    def __init__(self):
        self.mask = 0
        self.lock = threading.Lock()
        self.tap_lock = threading.Lock()
        self.enabled = sys.platform == "win32"
        self._last_error = 0.0
        if not self.enabled:
            log("非 Windows 环境, 外键只记录不会真的按键")

    def set_mask(self, mask):
        mask &= 0xFF
        with self.lock:
            changed = self.mask ^ mask
            if not changed:
                return
            for index, vk in enumerate(BUTTON_VKS):
                bit = 1 << index
                if changed & bit:
                    self._send(vk, down=bool(mask & bit))
            self.mask = mask
            pressed = [str(i + 1) for i in range(len(BUTTON_VKS)) if (mask >> i) & 1]
            log("外键 " + (" ".join(pressed) if pressed else "无"))

    def release_all(self):
        self.set_mask(0)

    def tap(self, name, hold=None):
        """Momentary press for TEST / SERVICE / COIN / select (triangle) / card."""
        vk = SYSTEM_VKS.get(name)
        if vk is None:
            log(f"未知系统键: {name}")
            return
        label = SYSTEM_LABELS[name]
        if not self.enabled:
            log(f"非 Windows 环境, 不注入 {label}")
            return
        if hold is None:
            hold = SYSTEM_HOLDS.get(name, 0.09)
        with self.tap_lock:
            self._send(vk, True)
            time.sleep(hold)
            self._send(vk, False)
        log(f"系统键 {label}")

    def _send(self, vk, down):
        if not self.enabled:
            return
        event = _INPUT()
        event.type = _INPUT_KEYBOARD
        event.ki = _KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=0 if down else _KEYEVENTF_KEYUP,
            time=0,
            dwExtraInfo=0,
        )
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
        if not sent and time.time() - self._last_error > 10:
            self._last_error = time.time()
            log(f"按键注入失败 (SendInput={sent}), 检查权限或游戏是否在前台")


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
        self.port = port
        self.baud = baud
        self.ser = None
        self.lock = threading.Lock()
        self.connected = False
        self.mask = 0
        self.running = True
        # The game forgets the panel state when the link drops; resend the
        # current mask even if it did not change since the last write.
        self.needs_sync = True
        self._connect()

    def _connect(self):
        try:
            self.ser = open_serial(self.port, self.baud)
        except Exception as error:
            self.ser = None
            log(f"串口 {self.port} 打不开, 稍后重试: {error}")
            return False
        self.needs_sync = True
        if isinstance(self.ser, DrySerial):
            self.connected = True
        return True

    def start(self):
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()

    def _reader(self):
        buf = bytearray()
        while self.running:
            if self.ser is None:
                time.sleep(2)
                with self.lock:
                    if self.ser is None:
                        self._connect()
                continue
            try:
                waiting = self.ser.in_waiting
                if not waiting:
                    time.sleep(0.002)
                    continue
                buf += self.ser.read(waiting)
            except Exception as error:  # serial disappears when the game exits
                log(f"串口读取失败: {error}")
                self.connected = False
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                time.sleep(0.5)
                continue

            while len(buf) >= 6:
                chunk = buf[:6]
                marker = chunk[3]
                if marker in (ord("r"), ord("k")):
                    reply = bytes([0x28, chunk[1], chunk[2], chunk[3], chunk[4], 0x29])
                    self._raw_write(reply)
                    log("握手 " + reply.hex(" "))
                    self.needs_sync = True
                    del buf[:6]
                elif marker == ord("A"):
                    if not self.connected:
                        log("游戏已接受触摸板")
                    self.connected = True
                    self.needs_sync = True
                    del buf[:6]
                elif marker in (ord("L"), ord("E")):
                    if self.connected:
                        log("触摸板已断开")
                    self.connected = False
                    self.needs_sync = True
                    del buf[:6]
                else:
                    del buf[:1]

    def _writer(self):
        last = None
        while self.running:
            packet = build_packet(self.mask)
            if self.connected and (packet != last or self.needs_sync):
                self._raw_write(packet)
                last = packet
                self.needs_sync = False
            time.sleep(0.004)

    def _raw_write(self, data):
        with self.lock:
            if self.ser is None:
                return
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
    """TCP client to the forwarded iPad port.

    `pymobiledevice3 usbmux forward` accepts local connections even when no
    device is behind it, so a successful TCP connect proves nothing. The link
    only counts as connected after the app's first frame arrives.
    """

    def __init__(self, host, port, on_mask, on_buttons, on_system):
        self.host = host
        self.port = port
        self.on_mask = on_mask
        self.on_buttons = on_buttons
        self.on_system = on_system
        self.running = True
        self.sock = None
        self.linked = False
        self.connected = False
        self.buttons = 0
        self.last_frame = 0.0
        self.last_connect = 0.0
        self.last_connect_log = 0.0
        self.send_lock = threading.Lock()

    @property
    def forward_up(self):
        """Forward port answered recently, even if it drops us straight away."""
        return self.linked or time.time() - self.last_connect < 15

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
            self.linked = True
            self.last_connect = time.time()
            self.last_frame = time.time()
            if time.time() - self.last_connect_log > 10:
                self.last_connect_log = time.time()
                log(f"转发端口已连上 {self.host}:{self.port}, 等待 iPad 数据")
            try:
                self._read_frames(sock)
            except OSError:
                pass
            finally:
                self.linked = False
                if self.connected:
                    log("iPad 连接断开")
                self.connected = False
                self.on_mask(0)
                self.on_buttons(0)
                try:
                    sock.close()
                except OSError:
                    pass
                self.sock = None
                time.sleep(1)

    def _read_frames(self, sock):
        sock.settimeout(1.0)
        buf = bytearray()
        while self.running:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                silent = time.time() - self.last_frame
                if silent > 5:
                    log("iPad 无数据, 重新连接")
                    return
                continue
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
                self.last_frame = time.time()
                if not self.connected:
                    self.connected = True
                    log("iPad 数据已到达, 链路确认")
                if frame_type == FRAME_TOUCH and len(payload) >= 5:
                    self.on_mask(int.from_bytes(payload[:5], "little"))
                elif frame_type == FRAME_BUTTONS and payload:
                    self.buttons = payload[0]
                    self.on_buttons(self.buttons)
                elif frame_type == FRAME_SYSTEM and payload:
                    key = SYSTEM_FRAME_KEYS.get(payload[0])
                    if key:
                        self.on_system(key)
                elif frame_type == FRAME_PING:
                    self.send_frame(FRAME_PONG, b"")

    def send_frame(self, frame_type, payload):
        """Queue one frame for the iPad; safe to call from any thread."""
        with self.send_lock:
            sock = self.sock
            if sock is None or not self.connected:
                return False
            header = FRAME_MAGIC + bytes([1, frame_type]) + struct.pack("<H", len(payload))
            try:
                sock.sendall(header + payload)
                return True
            except OSError:
                return False

    def send_led(self, payload):
        return self.send_frame(FRAME_LED, payload)


class LedLink:
    """Receives LED snapshots from the MelonLoader mod over loopback UDP."""

    def __init__(self, port, app_link):
        self.port = port
        self.app_link = app_link
        self.running = True
        self.sock = None
        self.last_rx = 0.0
        self.reported = False

    def start(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", self.port))
            sock.settimeout(0.25)
        except OSError as error:
            log(f"灯光端口 {self.port} 打不开: {error}")
            return False
        self.sock = sock
        threading.Thread(target=self._loop, daemon=True).start()
        log(f"等待游戏灯光 127.0.0.1:{self.port}")
        return True

    @property
    def alive(self):
        return time.time() - self.last_rx < 2.0

    def _loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) != LED_UDP_SIZE or data[:4] != LED_MAGIC:
                continue
            if not self.reported:
                self.reported = True
                log("已收到游戏灯光数据")
            self.last_rx = time.time()
            self.app_link.send_led(data[5:5 + LED_PAYLOAD_SIZE])

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass


class MonitorHub:
    """Broadcasts bridge state as JSON lines to the PC monitor window.

    The monitor also writes commands back on the same socket, e.g.
    {"cmd": "tap", "key": "coin"} for the cabinet buttons.
    """

    def __init__(self, host, port, on_command=None):
        self.host = host
        self.port = port
        self.on_command = on_command
        self.clients = []
        self.lock = threading.Lock()
        self.running = True
        self.server = None
        self.last = None

    def start(self):
        try:
            self.server = socket.create_server((self.host, self.port))
        except OSError as error:
            log(f"监视端口 {self.port} 打开失败: {error}")
            return False
        threading.Thread(target=self._accept, daemon=True).start()
        log(f"监视窗口端口 {self.host}:{self.port}")
        return True

    def _accept(self):
        while self.running:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            client.settimeout(None)
            with self.lock:
                self.clients.append(client)
                last = self.last
            if last is not None:
                try:
                    client.sendall(last)
                except OSError:
                    pass
            threading.Thread(target=self._reader, args=(client,), daemon=True).start()
            log("监视窗口已连接")

    def _reader(self, client):
        buf = b""
        while self.running:
            try:
                chunk = client.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    command = json.loads(line)
                except ValueError:
                    continue
                if isinstance(command, dict) and command.get("cmd") == "tap" and self.on_command:
                    self.on_command(str(command.get("key", "")))
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
        try:
            client.close()
        except OSError:
            pass

    def publish(self, state):
        line = (json.dumps(state, ensure_ascii=False) + "\n").encode()
        self.last = line
        dead = []
        with self.lock:
            for client in self.clients:
                try:
                    client.sendall(line)
                except OSError:
                    dead.append(client)
            for client in dead:
                self.clients.remove(client)
                try:
                    client.close()
                except OSError:
                    pass

    def close(self):
        self.running = False
        try:
            self.server.close()
        except OSError:
            pass
        with self.lock:
            for client in self.clients:
                try:
                    client.close()
                except OSError:
                    pass
            self.clients.clear()


def status_loop(hub, serial_link, app_link, led_link):
    last = None
    while hub.running:
        mask = serial_link.mask
        state = {
            "mask": mask,
            "serial": bool(serial_link.connected),
            "app": bool(app_link.connected),
            "app_tcp": bool(app_link.forward_up),
            "zones": zone_names(mask),
            "buttons": app_link.buttons,
            "led": bool(led_link and led_link.alive),
        }
        if state != last:
            hub.publish(state)
            last = state
        time.sleep(0.01)


def start_forward(local_port, device_port, udid=None):
    try:
        from forward import UsbForward
    except ImportError as error:
        log(f"USB 转发不可用 (先 pip install pymobiledevice3): {error}")
        return None
    forward = UsbForward(local_port, device_port, udid=udid, log=log)
    forward.start()
    return forward


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="COM33")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=24870)
    parser.add_argument("--usb", action="store_true", help="顺带启动 usbmux 转发")
    parser.add_argument("--udid", default=None, help="多台设备时指定 iPad 的 UDID")
    parser.add_argument("--hub-port", type=int, default=24871,
                        help="监视窗口端口, 0 = 不广播")
    parser.add_argument("--led-port", type=int, default=LED_PORT,
                        help="游戏灯光 UDP 端口, 0 = 不接收")
    args = parser.parse_args()

    forward = start_forward(args.port, args.port, args.udid) if args.usb else None

    serial_link = SerialLink(args.serial, args.baud)
    injector = KeyInjector()

    def system_tap(name):
        # tap holds the key for 90 ms; keep the touch receive thread free.
        threading.Thread(target=injector.tap, args=(name,), daemon=True).start()

    app_link = AppLink(args.host, args.port, serial_link.set_mask, injector.set_mask,
                       system_tap)
    serial_link.start()
    app_link.start()
    led_link = None
    if args.led_port:
        led_link = LedLink(args.led_port, app_link)
        if not led_link.start():
            led_link = None
    hub = None
    if args.hub_port:
        hub = MonitorHub("127.0.0.1", args.hub_port, on_command=injector.tap)
        if hub.start():
            threading.Thread(target=status_loop, args=(hub, serial_link, app_link, led_link),
                             daemon=True).start()
        else:
            hub = None
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
                    f"{' '.join(zone_names(serial_link.mask))}  "
                    f"外键={injector.mask:#04x}")
    except KeyboardInterrupt:
        pass
    finally:
        app_link.running = False
        injector.release_all()
        serial_link.close()
        if led_link:
            led_link.close()
        if hub:
            hub.close()
        if forward:
            forward.running = False
        log("已退出")


if __name__ == "__main__":
    main()
