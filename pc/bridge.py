"""Bridge between the iPad touch panel app and the maimai touch panel serial ports.

    iPad app 1P (TCP server, port 24870)   iPad app 2P (TCP server, port 24880)
        <-- usbmux over USB -->  in-process forwards (pc/forward.py)
    this script (TCP clients 127.0.0.1:24870 / 24880 + two serial writers)
        <-- two virtual COM pairs (com0com) -->  Sinmai.exe on COM3 / COM4

Touch frames coming from the iPads are converted to the panel serial protocol:
0x28 <7 bytes, 5 zones per byte> 0x29, and the game's handshake is answered
so the panels are accepted when the game boots. SDEZ always talks to two
panels; COM3 is 1P and COM4 is 2P, the same mapping the cabinet uses.

Each iPad announces its slot in the app (菜单 -> P1/P2) and listens on the
matching port, so the two usbmux forwards pair themselves with the right
device at startup.

Run:
    python bridge.py --usb
    python bridge.py --serial COM33 --serial2 COM34 --usb
    python bridge.py --serial COM33 --serial2 ""       (1P only)
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
FRAME_CONTROL = 0x07
FRAME_VIDEO = 0x10

# pc/ledmod/MaiPanelLed.cs sends 33-byte datagrams; byte 4 is the player
# (1 = 1P, 2 = 2P) and bytes 5.. carry the 28 bytes the iPad needs
# (flags, 8 x RGB, body/circle/side brightness).
LED_MAGIC = b"MPL1"
LED_PORT = 24872
LED_UDP_SIZE = 33
LED_PAYLOAD_SIZE = 28

# Payload id from the iPad's system-key frame -> SYSTEM_VKS name.
SYSTEM_FRAME_KEYS = {1: "test", 2: "service", 3: "coin", 4: "select",
                     5: "card", 6: "close"}

# Outer buttons 1..8 -> keyboard virtual key codes. 1P matches AquaMai
# KeyMap Button*_1P (E D C X Z A Q W), 2P matches Button*_2P (Keypad
# 8 9 6 3 2 1 4 7). Both mappings are configured in AquaMai.toml.
BUTTON_VKS = {
    1: (0x45, 0x44, 0x43, 0x58, 0x5A, 0x41, 0x51, 0x57),
    2: (0x68, 0x69, 0x66, 0x63, 0x62, 0x61, 0x64, 0x67),
}

# Cabinet operator keys, tapped from the monitor window. test/service/coin are
# shared cabinet keys; select follows AquaMai Select_1P / Select_2P. Only the
# 2P select differs, because the game reads it from the 2P input block.
SYSTEM_VKS = {
    1: {"test": 0x31, "service": 0x38, "coin": 0x33, "select": 0x32,
        "card": 0x0D},
    2: {"test": 0x31, "service": 0x38, "coin": 0x33, "select": 0x6A,
        "card": 0x0D},
}
SYSTEM_LABELS = {"test": "TEST", "service": "SERVICE", "coin": "投币", "select": "三角",
                 "card": "刷卡", "close": "关闭"}

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
    """Turns one iPad's outer-button bitmask into real keyboard presses."""

    def __init__(self, slot=1):
        self.slot = slot
        self.buttons = BUTTON_VKS[slot]
        self.mask = 0
        self.lock = threading.Lock()
        self.tap_lock = threading.Lock()
        self.enabled = sys.platform == "win32"
        self._last_error = 0.0
        if not self.enabled:
            log(f"{slot}P 非 Windows 环境, 外键只记录不会真的按键")

    def set_mask(self, mask):
        mask &= 0xFF
        with self.lock:
            changed = self.mask ^ mask
            if not changed:
                return
            for index, vk in enumerate(self.buttons):
                bit = 1 << index
                if changed & bit:
                    self._send(vk, down=bool(mask & bit))
            self.mask = mask
            pressed = [str(i + 1) for i in range(len(self.buttons)) if (mask >> i) & 1]
            log(f"{self.slot}P 外键 " + (" ".join(pressed) if pressed else "无"))

    def release_all(self):
        self.set_mask(0)

    def tap(self, name, hold=None):
        """Momentary press for TEST / SERVICE / COIN / select (triangle) / card."""
        if name == "close":
            self.close_window()
            return
        vk = SYSTEM_VKS[self.slot].get(name)
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
        log(f"{self.slot}P 系统键 {label}")

    def close_window(self):
        """Alt+F4; closes whichever window is in front, normally the game."""
        if not self.enabled:
            log("非 Windows 环境, 不注入 关闭")
            return
        with self.tap_lock:
            self._send(0x12, True)   # Alt
            self._send(0x73, True)   # F4
            self._send(0x73, False)
            self._send(0x12, False)
        log(f"{self.slot}P 系统键 关闭 (Alt+F4)")

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

    def __init__(self, slot=1):
        self.slot = slot
        self.is_open = True

    @property
    def in_waiting(self):
        return 0

    def read(self, size):
        return b""

    def write(self, data):
        log(f"{self.slot}P SERIAL " + data.hex(" "))

    def close(self):
        self.is_open = False


def open_serial(port, baud, slot=1):
    if port.strip().lower() in ("", "none"):
        log(f"{slot}P 未指定串口, 只打印数据包 (dry run)")
        return DrySerial(slot)
    return serial.Serial(port, baud, timeout=0.05)


class SerialLink:
    """Owns the panel side of the virtual COM pair."""

    def __init__(self, port, baud, slot=1):
        self.port = port
        self.baud = baud
        self.slot = slot
        self.ser = None
        self.lock = threading.Lock()
        self.connected = False
        self.mask = 0
        self.running = True
        # The game forgets the panel state when the link drops; resend the
        # current mask even if it did not change since the last write.
        self.needs_sync = True
        self._last_error_log = 0.0
        self._connect()

    def _connect(self):
        try:
            self.ser = open_serial(self.port, self.baud, self.slot)
        except Exception as error:
            self.ser = None
            if time.time() - self._last_error_log > 20:
                self._last_error_log = time.time()
                log(f"{self.slot}P 串口 {self.port} 打不开, 稍后重试: {error}")
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
                log(f"{self.slot}P 串口读取失败: {error}")
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
                    log(f"{self.slot}P 握手 " + reply.hex(" "))
                    self.needs_sync = True
                    del buf[:6]
                elif marker == ord("A"):
                    if not self.connected:
                        log(f"{self.slot}P 游戏已接受触摸板")
                    self.connected = True
                    self.needs_sync = True
                    del buf[:6]
                elif marker in (ord("L"), ord("E")):
                    if self.connected:
                        log(f"{self.slot}P 触摸板已断开")
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
                log(f"{self.slot}P 串口写入失败: {error}")

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

    def __init__(self, host, port, on_mask, on_buttons, on_system,
                 on_control=None, slot=1):
        self.host = host
        self.port = port
        self.slot = slot
        self.on_mask = on_mask
        self.on_buttons = on_buttons
        self.on_system = on_system
        self.on_control = on_control
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
            # Video frames are tiny but back-to-back; without this the round
            # trip picks up a 40 ms Nagle/delayed-ACK stall.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = sock
            self.linked = True
            self.last_connect = time.time()
            self.last_frame = time.time()
            if time.time() - self.last_connect_log > 10:
                self.last_connect_log = time.time()
                log(f"{self.slot}P 转发端口已连上 {self.host}:{self.port}, 等待 iPad 数据")
            try:
                self._read_frames(sock)
            except OSError:
                pass
            finally:
                self.linked = False
                if self.connected:
                    log(f"{self.slot}P iPad 连接断开")
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
                    log(f"{self.slot}P iPad 无数据, 重新连接")
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
                    log(f"{self.slot}P iPad 数据已到达, 链路确认")
                if frame_type == FRAME_TOUCH and len(payload) >= 5:
                    self.on_mask(int.from_bytes(payload[:5], "little"))
                elif frame_type == FRAME_BUTTONS and payload:
                    self.buttons = payload[0]
                    self.on_buttons(self.buttons)
                elif frame_type == FRAME_SYSTEM and payload:
                    key = SYSTEM_FRAME_KEYS.get(payload[0])
                    if key:
                        self.on_system(key)
                elif frame_type == FRAME_CONTROL and len(payload) >= 2 and self.on_control:
                    self.on_control(payload[0], payload[1])
                elif frame_type == FRAME_PING:
                    self.send_frame(FRAME_PONG, b"")

    def send_frame(self, frame_type, payload, wait=None):
        """Queue one frame for the iPad; safe to call from any thread.

        `wait` lets the video thread give up instead of queueing behind the
        touch frames: a mirrored frame is worthless once it is late.
        """
        if wait is None:
            self.send_lock.acquire()
        elif not self.send_lock.acquire(timeout=wait):
            return False
        try:
            sock = self.sock
            if sock is None or not self.connected:
                return False
            header = FRAME_MAGIC + bytes([1, frame_type]) + struct.pack("<H", len(payload))
            try:
                sock.sendall(header + payload)
                return True
            except OSError:
                return False
        finally:
            self.send_lock.release()

    def send_led(self, payload):
        return self.send_frame(FRAME_LED, payload)


class Player:
    """One iPad, one game panel port and one set of cabinet keys."""

    def __init__(self, slot, host, port, serial_port, baud=9600):
        self.slot = slot
        self.serial = SerialLink(serial_port, baud, slot)
        self.injector = KeyInjector(slot)
        self.video = None
        self.app = AppLink(host, port, self.serial.set_mask, self.injector.set_mask,
                           self._system_tap, on_control=self._control, slot=slot)

    def _system_tap(self, name):
        # tap holds the key for 90 ms; keep the touch receive thread free.
        threading.Thread(target=self.injector.tap, args=(name,), daemon=True).start()

    def _control(self, cmd, value):
        if cmd == 1:
            if self.video is not None:
                self.video.set_enabled(self.slot, value)
        elif cmd == 2 and value and value != self.slot:
            log(f"{self.slot}P iPad 报告自己是 {value}P: 请检查 iPad 菜单里的 P1/P2")


class LedLink:
    """Receives LED snapshots from the MelonLoader mod over loopback UDP.

    The mod tags every packet with the player (byte 4), so both players get
    their own colours from the one socket.
    """

    def __init__(self, port, players):
        self.port = port
        self.players = players
        self.running = True
        self.sock = None
        self.last_rx = {}
        self.reported = set()

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

    def alive(self, slot):
        return time.time() - self.last_rx.get(slot, 0.0) < 2.0

    @property
    def alive_any(self):
        return any(self.alive(slot) for slot in self.players)

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
            # 1 = 1P, 2 = 2P; an old mod that still writes 1 keeps working.
            slot = 2 if data[4] == 2 else 1
            player = self.players.get(slot)
            if player is None:
                continue
            if slot not in self.reported:
                self.reported.add(slot)
                log(f"已收到 {slot}P 游戏灯光数据")
            self.last_rx[slot] = time.time()
            player.app.send_led(data[5:5 + LED_PAYLOAD_SIZE])

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass


class VideoHub:
    """One capture thread that mirrors the game to every player that asked.

    In 1P the window holds a single 9:16 picture. When a second player joins,
    the game draws both views side by side (18:16), so the capture is split
    down the middle: left = 1P, right = 2P.
    """

    def __init__(self, players, max_width=0, fps=60, quality=55, layout="auto"):
        self.players = players
        self.max_width = max_width
        self.interval = 1.0 / max(1, fps)
        self.quality = quality
        self.layout = layout
        self.enabled = {slot: False for slot in players}
        self.running = True
        self.capture = None
        self.frames = 0
        self.dropped = 0

    def start(self):
        try:
            from screen_capture import GameCapture
        except Exception as error:
            log(f"画面捕获不可用: {error}")
            return False
        self.capture = GameCapture(max_width=self.max_width,
                                   quality=self.quality, log=log,
                                   layout=self.layout)
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    @property
    def window_found(self):
        return bool(self.capture and self.capture.window_found)

    @property
    def enabled_any(self):
        return any(self.enabled.values())

    def is_enabled(self, slot):
        return bool(self.enabled.get(slot))

    def set_enabled(self, slot, on):
        on = bool(on)
        if self.enabled.get(slot) == on:
            return
        self.enabled[slot] = on
        log(f"{slot}P 画面镜像 " + ("开" if on else "关"))

    def _loop(self):
        deadline = time.perf_counter()
        while self.running:
            targets = [player for player in self.players.values()
                       if self.enabled[player.slot] and player.app.connected]
            if not targets:
                time.sleep(0.05)
                deadline = time.perf_counter()
                continue
            frames = self.capture.grab_players([player.slot for player in targets])
            for player in targets:
                data = frames.get(player.slot)
                if data is None:
                    continue
                if player.app.send_frame(FRAME_VIDEO, data, wait=0.01):
                    self.frames += 1
                    if self.frames == 1:
                        log(f"画面已开始推流 ({len(data)} 字节/帧, "
                            f"{1 / self.interval:.0f} fps)")
                else:
                    self.dropped += 1
            # Keep a fixed pace: sleeping the remainder keeps the stream at
            # fps without letting a slow frame shift every later one.
            deadline += self.interval
            delay = deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.perf_counter()

    def close(self):
        self.running = False
        if self.capture is not None:
            self.capture.close()


class MonitorHub:
    """Broadcasts bridge state as JSON lines to the PC monitor window.

    The monitor also writes commands back on the same socket, e.g.
    {"cmd": "tap", "key": "coin", "slot": 1} for the cabinet buttons.
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
                    try:
                        slot = int(command.get("slot", 1))
                    except (TypeError, ValueError):
                        slot = 1
                    self.on_command(str(command.get("key", "")), slot)
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


def player_state(player, video=None):
    return {
        "slot": player.slot,
        "mask": player.serial.mask,
        "serial": bool(player.serial.connected),
        "app": bool(player.app.connected),
        "app_tcp": bool(player.app.forward_up),
        "zones": zone_names(player.serial.mask),
        "buttons": player.app.buttons,
        "video": bool(video and video.is_enabled(player.slot)),
    }


def status_loop(hub, players, led_link=None, video=None):
    last = None
    primary = players.get(1) or next(iter(players.values()))
    while hub.running:
        state = {
            # The monitor window was written for a single player; keep its
            # original keys pointed at 1P and publish the rest per player.
            "mask": primary.serial.mask,
            "serial": bool(primary.serial.connected),
            "app": bool(primary.app.connected),
            "app_tcp": bool(primary.app.forward_up),
            "zones": zone_names(primary.serial.mask),
            "buttons": primary.app.buttons,
            "led": bool(led_link and led_link.alive(primary.slot)),
            "video": bool(video and video.enabled_any),
            "video_window": bool(video and video.window_found),
            "layout": (video.capture.layout if video and video.capture else "off"),
            "players": [player_state(player, video) for player in players.values()],
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
    forward = UsbForward(local_port, device_port, udid=udid, log=log, strict=True)
    forward.start()
    return forward


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="COM33", help="1P 触摸板串口 (游戏用 COM3)")
    parser.add_argument("--serial2", default="COM34",
                        help="2P 触摸板串口 (游戏用 COM4), 传空字符串表示只玩 1P")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=24870, help="1P iPad 端口")
    parser.add_argument("--port2", type=int, default=24880, help="2P iPad 端口")
    parser.add_argument("--usb", action="store_true", help="顺带启动 usbmux 转发")
    parser.add_argument("--udid", default=None, help="1P iPad 的 UDID")
    parser.add_argument("--udid2", default=None, help="2P iPad 的 UDID")
    parser.add_argument("--hub-port", type=int, default=24871,
                        help="监视窗口端口, 0 = 不广播")
    parser.add_argument("--led-port", type=int, default=LED_PORT,
                        help="游戏灯光 UDP 端口, 0 = 不接收")
    parser.add_argument("--video-width", type=int, default=0,
                        help="镜像到 iPad 的画面宽度, 0 = 游戏窗口原分辨率")
    parser.add_argument("--video-fps", type=int, default=60,
                        help="画面帧率, 0 = 不启动画面镜像")
    parser.add_argument("--video-layout", choices=("auto", "single", "dual"),
                        default="auto", help="画面布局, auto = 按画面内容判断")
    args = parser.parse_args()

    players = {1: Player(1, args.host, args.port, args.serial, args.baud)}
    if args.serial2.strip():
        players[2] = Player(2, args.host, args.port2, args.serial2, args.baud)

    forwards = []
    if args.usb:
        forwards.append(start_forward(args.port, args.port, args.udid))
        if 2 in players:
            forwards.append(start_forward(args.port2, args.port2, args.udid2))

    video = None
    if args.video_fps:
        video = VideoHub(players, max_width=args.video_width, fps=args.video_fps,
                         layout=args.video_layout)
        if not video.start():
            video = None
    for player in players.values():
        player.video = video

    led_link = None
    if args.led_port:
        led_link = LedLink(args.led_port, players)
        if not led_link.start():
            led_link = None

    hub = None
    if args.hub_port:
        def hub_command(key, slot=1):
            player = players.get(slot)
            if player is not None:
                threading.Thread(target=player.injector.tap, args=(key,),
                                 daemon=True).start()

        hub = MonitorHub("127.0.0.1", args.hub_port, on_command=hub_command)
        if hub.start():
            threading.Thread(target=status_loop,
                             args=(hub, players, led_link, video),
                             daemon=True).start()
        else:
            hub = None

    for player in players.values():
        player.serial.start()
        player.app.start()

    model = " / ".join(f"{slot}P {player.app.port}" for slot, player in players.items())
    log(f"串口 {args.serial}@{args.baud} 就绪, 等待 iPad: {model}")

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
                for player in players.values():
                    player.serial.set_mask(0)
                log("清空触摸")
            elif line == "s":
                for player in players.values():
                    log(f"{player.slot}P 串口连接: {player.serial.connected} "
                        f"mask={player.serial.mask:#x} "
                        f"{' '.join(zone_names(player.serial.mask))}  "
                        f"外键={player.injector.mask:#04x}")
    except KeyboardInterrupt:
        pass
    finally:
        for player in players.values():
            player.app.running = False
            player.injector.release_all()
            player.serial.close()
        if video:
            video.close()
        if led_link:
            led_link.close()
        if hub:
            hub.close()
        for forward in forwards:
            if forward:
                forward.running = False
        log("已退出")


if __name__ == "__main__":
    main()
