"""Grabs the game window and turns it into small JPEG frames for the iPad.

Only Pillow is used. The window is located by process name (Sinmai.exe), so the
result does not depend on the window title or on where the window sits.
The client area is cropped to 9:16 and scaled down; frames stay far below the
65535 byte limit of the MPNL length field.

    python screen_capture.py --save probe.jpg     # one probe capture
"""

import argparse
import ctypes
import io
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

GAME_EXE = "sinmai.exe"
ASPECT = 9.0 / 16.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _dpi_aware():
    # Without this the monitor coordinates are virtualized on scaled displays
    # and the capture box drifts away from the real window.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


if sys.platform == "win32":
    _dpi_aware()


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
user32.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)


def pids_of(exe_name):
    pids = set()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return pids
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                pids.add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def find_game_window(exe_name=GAME_EXE):
    """Largest visible, non-minimized window owned by the game process."""
    pids = pids_of(exe_name)
    if not pids:
        return None
    best = []

    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        rect = _RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= 320 and height >= 320:
            best.append((width * height, hwnd))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return max(best)[1] if best else None


def client_box(hwnd):
    """Client area in screen pixels: (left, top, right, bottom)."""
    rect = _RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = _POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 320 or height < 320:
        return None
    return (origin.x, origin.y, origin.x + width, origin.y + height)


def crop_aspect(image, aspect=ASPECT):
    """Center-crop to the game's portrait aspect, dropping letterbox bars."""
    width, height = image.size
    if abs(width / height - aspect) < 0.005:
        return image
    if width / height > aspect:
        new_width = int(round(height * aspect))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(round(width / aspect))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


class GameCapture:
    def __init__(self, max_width=432, quality=55, exe_name=GAME_EXE, log=None):
        self.max_width = max_width
        self.quality = quality
        self.exe_name = exe_name
        self.log = log or (lambda text: None)
        self.hwnd = None
        self.window_found = False
        self.misses = 0
        self._last_warn = 0.0

    def _warn(self, text):
        if time.time() - self._last_warn > 5:
            self._last_warn = time.time()
            self.log(text)

    def _window(self):
        if self.hwnd and user32.IsWindow(self.hwnd) and not user32.IsIconic(self.hwnd):
            return self.hwnd
        self.hwnd = find_game_window(self.exe_name)
        self.window_found = self.hwnd is not None
        return self.hwnd

    def grab(self):
        """One JPEG frame, or None while the game window is unavailable."""
        hwnd = self._window()
        if not hwnd:
            self.misses += 1
            self._warn("等待 Sinmai 窗口")
            return None
        box = client_box(hwnd)
        if not box:
            self.misses += 1
            self._warn("游戏窗口最小化或太小")
            return None
        try:
            image = ImageGrab.grab(bbox=box, all_screens=True)
        except Exception as error:
            self.misses += 1
            self._warn(f"抓屏失败: {error}")
            return None
        image = crop_aspect(image.convert("RGB"))
        width = min(self.max_width, image.width)
        height = max(1, int(round(width / ASPECT)))
        image = image.resize((width, height), Image.BILINEAR)

        quality = self.quality
        while True:
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality, optimize=False)
            data = buffer.getvalue()
            if len(data) <= 60000 or quality <= 25:
                break
            quality -= 10
        return data if len(data) <= 65000 else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=GAME_EXE)
    parser.add_argument("--save", default=None, help="把一帧存成 JPEG 方便检查")
    parser.add_argument("--width", type=int, default=432)
    args = parser.parse_args()

    hwnd = find_game_window(args.exe)
    if not hwnd:
        print(f"没有找到 {args.exe} 的可见窗口")
        return 1
    print(f"窗口句柄 {hwnd} 客户区 {client_box(hwnd)}")
    if args.save:
        capture = GameCapture(max_width=args.width, exe_name=args.exe,
                              log=lambda text: print(text))
        data = capture.grab()
        if not data:
            print("抓屏失败")
            return 1
        with open(args.save, "wb") as handle:
            handle.write(data)
        print(f"已保存 {args.save} ({len(data)} 字节)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
