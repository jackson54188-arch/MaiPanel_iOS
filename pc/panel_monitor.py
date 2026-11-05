"""PC-side self test window: lights up the maimai touch zones pressed on the iPad.

    python panel_monitor.py             # live, bridge.py must be running
    python panel_monitor.py --demo      # cycle all 34 zones, no bridge needed

bridge.py broadcasts its state as JSON lines on 127.0.0.1:24871 (hub port).
"""

import argparse
import ctypes
import json
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk
except ImportError:
    sys.exit("Pillow 缺失, 请先 pip install pillow")


def resource_dir():
    if getattr(sys, "frozen", False):  # PyInstaller one-file bundle
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parent.parent / "app" / "Sources" / "Resources"


RES = resource_dir()
MASK_PATH = RES / "panel_mask.png"
CENTERS_PATH = RES / "zone_centers.json"

ZONE_ORDER = (
    [f"A{i}" for i in range(1, 9)]
    + [f"B{i}" for i in range(1, 9)]
    + ["C1", "C2"]
    + [f"D{i}" for i in range(1, 9)]
    + [f"E{i}" for i in range(1, 9)]
)

BG = "#000000"
TEXT = "#f2f2f2"
DIM = "#8a8a8a"


class PanelArt:
    """Monochrome disc: dark zone shapes on black, white when pressed."""

    def __init__(self, size):
        self.size = size
        src = Image.open(MASK_PATH).convert("RGB")
        width, height = src.size
        centers = json.loads(CENTERS_PATH.read_text(encoding="utf-8"))
        pixels = src.load()

        colors = {}
        for name in ZONE_ORDER:
            centre = centers[name]
            colors[name] = pixels[int(centre["x"] * width), int(centre["y"] * height)]

        small = src.resize((size, size), Image.LANCZOS)
        palette = [0, 0, 0]
        for name in ZONE_ORDER:
            palette += list(colors[name])
        palette += [0] * (768 - len(palette))
        palette_image = Image.new("P", (1, 1))
        palette_image.putpalette(palette)
        index = small.quantize(palette=palette_image, dither=Image.NONE).tobytes()

        # Nearest-colour assignment also grabs thin outlines of similarly
        # coloured far-away zones, so keep only the component around each centre.
        self.masks = {}
        for i, name in enumerate(ZONE_ORDER, start=1):
            cx = int(centers[name]["x"] * size)
            cy = int(centers[name]["y"] * size)
            start = cy * size + cx
            if index[start] != i:
                start = self._nearby(index, size, start, i)
            region = self._region(index, size, start, i)
            self.masks[name] = region.filter(ImageFilter.GaussianBlur(0.7))

        self.base = Image.new("RGB", (size, size), (0, 0, 0))
        for mask in self.masks.values():
            self.base.paste((18, 18, 18), (0, 0), mask)

        self.font = self._load_font(max(12, size // 44))
        self.label_pos = {
            name: (int(c["x"] * size), int(c["y"] * size))
            for name, c in centers.items()
        }

    @staticmethod
    def _nearby(index, size, start, wanted):
        x0, y0 = start % size, start // size
        for radius in range(1, 8):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    x, y = x0 + dx, y0 + dy
                    if 0 <= x < size and 0 <= y < size and index[y * size + x] == wanted:
                        return y * size + x
        return -1

    @staticmethod
    def _region(index, size, start, wanted):
        mask = bytearray(size * size)
        if start < 0:
            return Image.new("L", (size, size))
        stack = [start]
        mask[start] = 255
        while stack:
            p = stack.pop()
            if p >= size:
                q = p - size
                if index[q] == wanted and not mask[q]:
                    mask[q] = 255
                    stack.append(q)
            if p + size < len(index):
                q = p + size
                if index[q] == wanted and not mask[q]:
                    mask[q] = 255
                    stack.append(q)
            if p % size:
                q = p - 1
                if index[q] == wanted and not mask[q]:
                    mask[q] = 255
                    stack.append(q)
            if p % size != size - 1:
                q = p + 1
                if index[q] == wanted and not mask[q]:
                    mask[q] = 255
                    stack.append(q)
        return Image.frombytes("L", (size, size), bytes(mask))

    @staticmethod
    def _load_font(size):
        for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def render(self, active):
        image = self.base.copy()
        if active:
            glow = Image.new("RGB", (self.size, self.size))
            for name in active:
                mask = self.masks.get(name)
                if mask is None:
                    continue
                image.paste((255, 255, 255), (0, 0), mask)
                glow.paste((255, 255, 255), (0, 0), mask)
            glow = glow.filter(ImageFilter.GaussianBlur(20))
            glow = Image.eval(glow, lambda v: v * 140 // 255)
            image = ImageChops.add(image, glow)

        draw = ImageDraw.Draw(image)
        for name, xy in self.label_pos.items():
            if name in active:
                draw.text(xy, name, font=self.font, fill=(0, 0, 0), anchor="mm")
            else:
                draw.text(xy, name, font=self.font, fill=(238, 238, 238), anchor="mm")
        return image


class HubClient(threading.Thread):
    """Keeps the latest state published by bridge.py."""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.running = True
        self.lock = threading.Lock()
        self.latest = None

    def post(self, state):
        with self.lock:
            self.latest = state

    def take(self):
        with self.lock:
            state = self.latest
            self.latest = None
            return state

    def run(self):
        while self.running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=1.5)
            except OSError:
                self.post({"hub": False})
                time.sleep(1.0)
                continue
            sock.settimeout(None)
            self.post({"hub": True})
            buf = b""
            try:
                while self.running:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            state = json.loads(line)
                        except ValueError:
                            continue
                        state["hub"] = True
                        self.post(state)
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
                self.post({"hub": False})

    def stop(self):
        self.running = False


class MonitorApp:
    def __init__(self, root, art, host, port, demo):
        self.root = root
        self.art = art
        self.demo = demo
        self.client = None if demo else HubClient(host, port)
        self.mask = 0
        self.demo_index = 0
        self.demo_at = time.time()
        self.photo = None

        root.title("MaiPanel 触摸板自检")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.screen = tk.Label(root, bd=0, highlightthickness=0, bg=BG)
        self.screen.pack(padx=16, pady=(16, 8))

        status = tk.Frame(root, bg=BG)
        status.pack(fill="x", padx=18, pady=(0, 14))

        tk.Label(status, text="连接状态", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 11, "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.rows = {}
        specs = (("hub", "桥接程序"), ("serial", "游戏串口"), ("app", "iPad 面板"))
        for row, (key, label) in enumerate(specs, start=1):
            tk.Label(status, text=label, bg=BG, fg=DIM, anchor="w",
                     font=("Microsoft YaHei UI", 10)).grid(row=row, column=0, sticky="w", pady=1)
            value = tk.Label(status, text="—", bg=BG, fg=DIM, anchor="w",
                             font=("Microsoft YaHei UI", 10, "bold"))
            value.grid(row=row, column=1, sticky="w", padx=(20, 0))
            self.rows[key] = value

        self.mask_label = tk.Label(status, text="MASK 0x0000000000", bg=BG, fg=TEXT,
                                   font=("Consolas", 10), anchor="w")
        self.mask_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.zone_label = tk.Label(status, text="—", bg=BG, fg=DIM, anchor="w",
                                   justify="left", wraplength=art.size,
                                   font=("Consolas", 10))
        self.zone_label.grid(row=5, column=0, columnspan=2, sticky="w")
        self.hint = tk.Label(status, text="", bg=BG, fg=DIM, anchor="w", justify="left",
                             wraplength=art.size, font=("Microsoft YaHei UI", 9))
        self.hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        root.bind("<Escape>", lambda _event: root.destroy())
        self._draw(0)
        if self.client:
            self.client.start()
        self._poll()

    def _draw(self, mask):
        self.mask = mask
        active = [name for i, name in enumerate(ZONE_ORDER) if (mask >> i) & 1]
        image = self.art.render(active)
        self.photo = ImageTk.PhotoImage(image)
        self.screen.configure(image=self.photo)
        self.mask_label.config(text=f"MASK 0x{mask:010X}")

    def _apply(self, state):
        changed_mask = state.get("mask", 0) != self.mask
        hub = bool(state.get("hub"))
        serial = bool(state.get("serial"))
        app = bool(state.get("app"))
        app_tcp = bool(state.get("app_tcp"))

        self.rows["hub"].config(text="已连接" if hub else "未运行",
                                fg=TEXT if hub else DIM)
        self.rows["serial"].config(text="已握手" if serial else "等待游戏",
                                   fg=TEXT if serial else DIM)
        if app:
            app_text = "已连接"
        elif app_tcp:
            app_text = "无数据（检查 USB 转发）"
        else:
            app_text = "等待 iPad"
        self.rows["app"].config(text=app_text, fg=TEXT if app else DIM)

        zones = state.get("zones") or []
        self.zone_label.config(text=" ".join(zones) if zones else "—",
                               fg=TEXT if zones else DIM)
        self.hint.config(text=self._hint(state))

        if changed_mask:
            self._draw(state.get("mask", 0))

    def _hint(self, state):
        if self.demo:
            return "演示模式：自动轮流点亮 34 个区域。"
        if not state.get("hub"):
            return "桥接程序未运行：先双击 bridge.bat 启动桥接。"
        if state.get("app_tcp") and not state.get("app"):
            return "USB 转发端口连上了，但 iPad 没有回数据：确认 iPad 已解锁、数据线可传输，并让 App 保持在前台。"
        if not state.get("app"):
            return "等待 iPad：确认 USB 已连接，且桥接启动了 usbmux 转发。"
        if not state.get("serial"):
            return "iPad 已连上，等待游戏接受手台：启动 Sinmai 后自动握手。"
        return "触摸数据实时同步中。"

    def _poll(self):
        if self.demo:
            now = time.time()
            if now >= self.demo_at:
                self.demo_at = now + 0.45
                index = self.demo_index % len(ZONE_ORDER)
                self.demo_index += 1
                self._apply({"hub": True, "serial": True, "app": True, "app_tcp": True,
                             "mask": 1 << index, "zones": [ZONE_ORDER[index]]})
        elif self.client:
            state = self.client.take()
            if state is not None:
                self._apply(state)
        self.root.after(30, self._poll)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="bridge.py 地址")
    parser.add_argument("--port", type=int, default=24871, help="bridge.py 监视端口")
    parser.add_argument("--size", type=int, default=0, help="触摸区像素尺寸, 默认按屏幕自动")
    parser.add_argument("--demo", action="store_true", help="不连 bridge, 自动循环 34 区")
    args = parser.parse_args()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    size = args.size or min(720, screen_h - 340, screen_w - 60)
    art = PanelArt(size)
    MonitorApp(root, art, args.host, args.port, args.demo)

    root.update_idletasks()
    width = max(size + 32, root.winfo_reqwidth())
    height = root.winfo_reqheight()
    root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 2)}")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror("MaiPanel", traceback.format_exc())
        except Exception:
            pass
        raise
