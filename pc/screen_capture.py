"""Grabs the game window and turns it into small JPEG frames for the iPad.

The fast path is the Desktop Duplication API (dxcam): the composited frame is
already on the GPU, so one grab costs well under a millisecond instead of the
16 ms a screen DC read stalls on. OpenCV then resizes and encodes in about
3 ms. GDI StretchBlt and Pillow ImageGrab stay as fallbacks.

    python screen_capture.py --save probe.jpg       # one probe capture
    python screen_capture.py --exe sinmai.exe       # benchmark the game window
"""

import argparse
import ctypes
import io
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageFilter, ImageGrab

try:
    import numpy as np
except ImportError:  # pragma: no cover - fallbacks below still work
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import dxcam
except ImportError:
    dxcam = None

GAME_EXE = "sinmai.exe"
ASPECT = 9.0 / 16.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


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


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
user32.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = (wintypes.HWND,)
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = (
    wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.StretchBlt.argtypes = (
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
gdi32.SetBrushOrgEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                ctypes.POINTER(_POINT))


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

    user32.EnumWindows(
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
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


def crop_aspect(box, aspect):
    """Center-crop a screen box to `aspect` without touching the outside."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if width / height > aspect:
        new_width = int(round(height * aspect))
        left += (width - new_width) // 2
        width = new_width
    else:
        new_height = int(round(width / aspect))
        top += (height - new_height) // 2
        height = new_height
    return (left, top, left + width, top + height)


def crop_9_16(box):
    """Center-crop a screen box to the game's portrait aspect."""
    return crop_aspect(box, ASPECT)


class FastGrabber:
    """StretchBlt from the screen straight into a DIB at the output size."""

    SRCCOPY = 0x00CC0020
    HALFTONE = 4
    COLORONCOLOR = 3

    def __init__(self, width, height, smooth=True):
        self.width = width
        self.height = height
        self.ok = False
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # top-down rows, so no vertical flip later
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
        info = _BITMAPINFO()
        info.bmiHeader = header
        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(
            self.mem_dc, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap:
            return
        gdi32.SelectObject(self.mem_dc, self.bitmap)
        gdi32.SetStretchBltMode(self.mem_dc, self.HALFTONE if smooth else self.COLORONCOLOR)
        gdi32.SetBrushOrgEx(self.mem_dc, 0, 0, None)
        self.ok = bool(self.screen_dc and self.mem_dc and self.bits)

    def grab(self, box, aspect=ASPECT):
        """PIL image of the box (centre-cropped to `aspect`), or None."""
        if not self.ok:
            return None
        left, top, right, bottom = crop_aspect(box, aspect) if aspect else box
        if right <= left or bottom <= top:
            return None
        if not gdi32.StretchBlt(self.mem_dc, 0, 0, self.width, self.height,
                                self.screen_dc, left, top, right - left, bottom - top,
                                self.SRCCOPY):
            return None
        data = ctypes.string_at(self.bits, self.width * self.height * 4)
        return Image.frombuffer("RGB", (self.width, self.height), data,
                                "raw", "BGRX", 0, 1)

    def close(self):
        try:
            if self.bitmap:
                gdi32.DeleteObject(self.bitmap)
            if self.mem_dc:
                gdi32.DeleteDC(self.mem_dc)
            if self.screen_dc:
                user32.ReleaseDC(None, self.screen_dc)
        except Exception:
            pass
        self.ok = False


class GameCapture:
    """Turns the game window into JPEG frames at most `max_width` wide.

    `max_width` of 0 keeps the window's own resolution: the portrait game strip
    is only ~608 px wide on a 1080p monitor, so upscaling would just cost bytes.
    """

    def __init__(self, max_width=0, quality=68, exe_name=GAME_EXE, log=None,
                 layout="auto", sharpen=0.3, use_camera=True):
        self.max_width = max_width
        self.quality = quality
        self.sharpen = sharpen
        self.use_camera = use_camera
        self.exe_name = exe_name
        self.log = log or (lambda text: None)
        self.hwnd = None
        self.window_found = False
        self.misses = 0
        self._last_warn = 0.0
        self._target = None
        self._gdi = None
        self._gdi_failed = False
        self.camera = None
        # "single" = one 9:16 picture, "dual" = 1P left / 2P right (18:16).
        # Auto detection samples the window twice a second; see _layout().
        self.layout = "single"
        self.forced_layout = layout if layout in ("single", "dual") else None
        self._layout_at = 0.0
        self._layout_streak = 0
        self._layout_guess = None
        self._sampler = None
        self._open_camera()

    def _open_camera(self):
        if not self.use_camera:
            self.log("按参数使用 GDI 抓屏")
            return False
        if dxcam is None or np is None:
            self.log("没有 dxcam, 退回 GDI 抓屏")
            return False
        try:
            self.camera = dxcam.create(output_color="BGR")
        except Exception as error:
            self.log(f"桌面复制不可用 ({error}), 退回 GDI 抓屏")
            self.camera = None
        return self.camera is not None

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

    def _target_size(self, crop):
        width = crop[2] - crop[0]
        if self.max_width and width > self.max_width:
            width = self.max_width
        width -= width % 2
        height = int(round(width / ASPECT))
        height -= height % 2
        return max(2, width), max(2, height)

    def _gdi_image(self, box, size):
        if self._gdi_failed:
            return None
        if self._gdi is None or (self._gdi.width, self._gdi.height) != size:
            if self._gdi is not None:
                self._gdi.close()
            self._gdi = FastGrabber(*size)
            if not self._gdi.ok:
                self._gdi_failed = True
                self.log("GDI 抓屏不可用, 退回 Pillow")
                return None
        return self._gdi.grab(box)

    def _slow_image(self, box, size):
        image = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
        left, top, right, bottom = crop_9_16((0, 0, image.width, image.height))
        image = image.crop((left, top, right, bottom))
        return image.resize(size, Image.BILINEAR)

    def _encode(self, image, size):
        # The iPad upscales the 608-wide picture to roughly twice its size, so
        # a mild unsharp mask recovers some of the edge contrast bilinear
        # scaling eats. Applied once; re-sharpening per quality step would pile
        # halos on top of each other.
        if self.sharpen:
            # GDI/Pillow hands back RGB; move it to the same BGR array path so
            # the unsharp mask does not cost 20 ms in pure Python.
            if (cv2 is not None and np is not None
                    and not isinstance(image, np.ndarray)):
                image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            if cv2 is not None and np is not None and isinstance(image, np.ndarray):
                blur = cv2.GaussianBlur(image, (0, 0), 1.0)
                image = cv2.addWeighted(image, 1.0 + self.sharpen, blur,
                                        -self.sharpen, 0)
            else:
                image = image.filter(ImageFilter.UnsharpMask(
                    radius=1, percent=int(self.sharpen * 100), threshold=0))
        quality = self.quality
        while True:
            if cv2 is not None and np is not None and isinstance(image, np.ndarray):
                ok, buffer = cv2.imencode(
                    ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
                data = buffer.tobytes() if ok else b""
            else:
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=quality, optimize=False)
                data = buffer.getvalue()
            # The frame header carries a u16 length and the app drops anything
            # over 65000 bytes, so the quality steps stop at 64 KB.
            if len(data) <= 64000 or quality <= 25:
                return data if data and len(data) <= 65000 else None
            quality -= 5

    def _content_ratio(self, box):
        """Fraction of the window width that is not a black letterbox bar."""
        width = box[2] - box[0]
        height = box[3] - box[1]
        if width <= 0 or height <= 0:
            return None
        sample_width = 160
        sample_height = max(16, int(round(sample_width * height / width)))
        if self._sampler is None or (self._sampler.width, self._sampler.height) != (
                sample_width, sample_height):
            if self._sampler is not None:
                self._sampler.close()
            self._sampler = FastGrabber(sample_width, sample_height, smooth=False)
            if not self._sampler.ok:
                self._sampler = None
                return None
        image = self._sampler.grab(box, aspect=None)
        if image is None:
            return None
        gray = image.convert("L")
        w, h = gray.size
        pixels = list(gray.getdata())
        need = max(2, h // 40)
        left = right = None
        for x in range(w):
            count = 0
            for value in pixels[x::w]:
                if value > 12:
                    count += 1
                    if count >= need:
                        if left is None:
                            left = x
                        right = x
                        break
        if left is None:
            return None
        return (right - left + 1) / w

    def _layout(self, box):
        if self.forced_layout:
            return self.forced_layout
        now = time.time()
        if now - self._layout_at < 0.5:
            return self.layout
        self._layout_at = now
        ratio = self._content_ratio(box)
        if ratio is None:
            return self.layout
        window_aspect = (box[2] - box[0]) / max(1, box[3] - box[1])
        guess = "dual" if window_aspect >= 0.75 and ratio > 0.5 else "single"
        if guess == self.layout:
            self._layout_streak = 0
            self._layout_guess = None
            return self.layout
        if guess != self._layout_guess:
            self._layout_guess = guess
            self._layout_streak = 0
        self._layout_streak += 1
        if self._layout_streak >= 3:
            self.layout = guess
            self._layout_streak = 0
            self._layout_guess = None
            self.log("画面布局: " + ("双人左右分屏 (左 1P / 右 2P)"
                                     if guess == "dual" else "单人"))
        return self.layout

    def grab_players(self, slots):
        """{slot: jpeg} for the requested players, empty when nothing changed."""
        hwnd = self._window()
        if not hwnd:
            self.misses += 1
            self._warn("等待 Sinmai 窗口")
            return {}
        box = client_box(hwnd)
        if not box:
            self.misses += 1
            self._warn("游戏窗口最小化或太小")
            return {}

        if self._layout(box) == "dual":
            content = crop_aspect(box, ASPECT * 2)
            split = content[0] + (content[2] - content[0]) // 2
            halves = {}
            if 1 in slots:
                halves[1] = (content[0], content[1], split, content[3])
            if 2 in slots:
                halves[2] = (split, content[1], content[2], content[3])
            return self._grab_halves(content, halves)

        # Single layout is one full-screen UI (menus, TEST, results). Both iPads
        # get the same picture instead of only 1P, which used to leave 2P blank.
        slots = [slot for slot in slots if slot in (1, 2)]
        if not slots:
            return {}
        return self._grab_one(crop_9_16(box), slots)

    def _grab_one(self, crop, slots):
        size = self._target_size(crop)
        if self._target != size:
            self._target = size

        def wrap(data):
            return {slot: data for slot in slots} if data else {}

        if self.camera is not None:
            try:
                # Returns None when the desktop has not changed since the last
                # grab; the iPad then simply keeps the frame it already has.
                frame = self.camera.grab(region=crop)
            except Exception as error:
                self.log(f"桌面复制抓帧失败 ({error}), 退回 GDI 抓屏")
                self.camera = None
                frame = None
            if frame is not None:
                if cv2 is not None and tuple(frame.shape[1::-1]) != size:
                    frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
                elif cv2 is None:
                    return wrap(self._encode(self._slow_from_array(frame, size), size))
                return wrap(self._encode(frame, size))
            if self.camera is not None:
                return {}
        image = self._gdi_image(crop, size) or self._slow_image(crop, size)
        return wrap(self._encode(image, size))

    def _grab_halves(self, content, halves):
        sizes = {slot: self._target_size(box) for slot, box in halves.items()}
        if self.camera is not None:
            try:
                # One grab of the whole 18:16 content, then split it: a second
                # grab would return None because the desktop did not change.
                frame = self.camera.grab(region=content)
            except Exception as error:
                self.log(f"桌面复制抓帧失败 ({error}), 退回 GDI 抓屏")
                self.camera = None
                frame = None
            if frame is not None:
                split = frame.shape[1] // 2
                out = {}
                for slot, size in sizes.items():
                    part = frame[:, :split] if slot == 1 else frame[:, split:]
                    if cv2 is not None:
                        if tuple(part.shape[1::-1]) != size:
                            part = cv2.resize(part, size, interpolation=cv2.INTER_AREA)
                        data = self._encode(part, size)
                    else:
                        data = self._encode(self._slow_from_array(part, size), size)
                    if data:
                        out[slot] = data
                return out
            if self.camera is not None:
                return {}
        out = {}
        for slot, box in halves.items():
            size = sizes[slot]
            image = self._gdi_image(box, size) or self._slow_image(box, size)
            data = self._encode(image, size)
            if data:
                out[slot] = data
        return out

    def grab(self):
        """One 1P JPEG frame, or None when nothing new is on screen."""
        return self.grab_players([1]).get(1)

    @staticmethod
    def _slow_from_array(frame, size):
        return Image.fromarray(frame[:, :, ::-1]).resize(size, Image.BILINEAR)

    def close(self):
        if self._gdi is not None:
            self._gdi.close()
            self._gdi = None
        if self._sampler is not None:
            self._sampler.close()
            self._sampler = None
        camera, self.camera = self.camera, None
        if camera is not None:
            try:
                camera.stop()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=GAME_EXE)
    parser.add_argument("--save", default=None, help="把一帧存成 JPEG 方便检查")
    parser.add_argument("--width", type=int, default=0, help="0 = 窗口原分辨率")
    parser.add_argument("--quality", type=int, default=68)
    parser.add_argument("--layout", choices=("auto", "single", "dual"),
                        default="auto", help="画面布局, auto = 按画面内容判断")
    parser.add_argument("--gdi", action="store_true",
                        help="跳过桌面复制, 直接用 GDI 抓屏 (画面里有黑块时对比用)")
    args = parser.parse_args()

    hwnd = find_game_window(args.exe)
    if not hwnd:
        print(f"没有找到 {args.exe} 的可见窗口")
        return 1
    print(f"窗口句柄 {hwnd} 客户区 {client_box(hwnd)}")
    capture = GameCapture(max_width=args.width, quality=args.quality,
                          exe_name=args.exe, log=lambda text: print(text),
                          layout=args.layout, use_camera=not args.gdi)

    # The desktop duplicator only hands out frames when something moved, so
    # keep a frame around and only time the ones that really arrived.
    data = None
    found = 0
    started = time.perf_counter()
    for _ in range(60):
        got = capture.grab()
        if got is not None:
            data = got
            found += 1
    spent = time.perf_counter() - started
    print(f"{found}/60 帧有新画面, {spent * 1000 / 60:.1f} ms/次, "
          f"每帧 {len(data) if data else 0} 字节, 布局 {capture.layout}")

    if args.save and data:
        with open(args.save, "wb") as handle:
            handle.write(data)
        print(f"已保存 {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
