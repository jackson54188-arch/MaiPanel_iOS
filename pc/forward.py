"""usbmux port forwarding with device selection and quiet retries.

`pymobiledevice3 usbmux forward` targets the first USB device and prints
"failed to connect to remote endpoint" for every refused connection. This
wrapper forwards to the iPad that actually has MaiPanel listening, retries
while the app is closed, and turns cable / trust problems into one message.
"""

import asyncio
import logging
import threading
import time


class UsbForward(threading.Thread):
    def __init__(self, local_port, device_port, udid=None, log=print, strict=False):
        super().__init__(daemon=True)
        self.local_port = local_port
        self.device_port = device_port
        self.udid = udid
        self.log = log
        self.running = True
        # Two iPads: only pair with a device that really listens on this
        # slot's port, otherwise both forwards grab the first iPad.
        self.strict = strict
        self._last_wait = 0.0

    def run(self):
        # One error line per refused connection would flood the console while
        # the app is closed; bridge.py already retries and reports state.
        logging.getLogger("pymobiledevice3.tcp_forwarder").setLevel(logging.CRITICAL)
        while self.running:
            try:
                asyncio.run(self._session())
            except Exception as error:
                self.log(f"USB 转发异常: {error}")
                time.sleep(2)

    async def _session(self):
        from pymobiledevice3 import usbmux
        from pymobiledevice3.tcp_forwarder import UsbmuxTcpForwarder

        device = await self._pick(usbmux)
        if device is None or not self.running:
            return
        self.log(f"USB 转发 127.0.0.1:{self.local_port} -> iPad {device.serial}")
        forwarder = UsbmuxTcpForwarder(device.serial, self.device_port, self.local_port)
        task = asyncio.create_task(forwarder.start())
        try:
            while self.running:
                await asyncio.sleep(1)
                if task.done():
                    break
                if not await self._present(usbmux, device.serial):
                    self.log("iPad 已断开, 等待重新连接")
                    break
        finally:
            forwarder.stop()
            try:
                await asyncio.wait_for(task, timeout=3)
            except asyncio.TimeoutError:
                task.cancel()
            except Exception:
                pass

        if task.done() and not task.cancelled():
            error = task.exception()
            if error:
                self.log(f"转发端口 {self.local_port} 打开失败: {error}")
                self.log("检查是否还有旧的 bridge / usbmux forward 占着这个端口")
                await asyncio.sleep(3)

    def _waiting(self, text):
        if time.time() - self._last_wait > 15:
            self._last_wait = time.time()
            self.log(text)

    async def _pick(self, usbmux):
        try:
            devices = await usbmux.list_devices()
        except Exception as error:
            self.log(f"连不上 usbmuxd: {error} (需要装 Apple Devices 或 iTunes 并保持运行)")
            await asyncio.sleep(3)
            return None
        devices = [device for device in devices if device.is_usb]
        if self.udid:
            devices = [device for device in devices if device.matches_udid(self.udid)]
        if not devices:
            self._waiting("没找到 USB 连接的 iPad: 换一根支持数据传输的线 "
                          "(有些 C-to-C 线只能充电), 解锁 iPad 并点「信任此电脑」")
            await asyncio.sleep(3)
            return None
        if len(devices) == 1:
            if not self.strict or await self._listening(devices[0]):
                return devices[0]
        else:
            for device in devices:
                if await self._listening(device):
                    return device
        if self.strict:
            self._waiting(f"没有 iPad 在端口 {self.device_port} 监听: "
                          "打开 App 并把菜单里的 P1/P2 设成对应玩家")
            await asyncio.sleep(3)
            return None
        self._waiting("发现多台设备, 先用 " + devices[0].serial + "; 可用 --udid 指定 iPad")
        return devices[0]

    async def _listening(self, device):
        try:
            sock = await device.connect(self.device_port)
        except Exception:
            return False
        try:
            sock.close()
        except OSError:
            pass
        return True

    async def _present(self, usbmux, serial):
        try:
            devices = await usbmux.list_devices()
        except Exception:
            return False
        return any(device.is_usb and device.matches_udid(serial) for device in devices)
