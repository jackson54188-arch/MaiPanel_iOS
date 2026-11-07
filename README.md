# MaiPanel

把 iPad 当成 maimai DX 的触摸手台。iPad 上是一整块 34 区圆盘，圆盘外侧
一圈是 8 个按键，圆盘上方是三角 / 刷卡 / 菜单三个键（TEST、SERVICE、投币
和面板开关收在菜单里），全部经同一根 USB 线发给电脑，由电脑转成游戏的
手台串口信号。游戏里的外键灯光也会同步回 iPad；声音留在电脑上，画面可以
在菜单里打开镜像（调试用）。

- `app/` — iPad App（SwiftUI）
- `pc/` — 电脑侧桥接、USB 转发和自检窗口
- `pc/ledmod/` — 从游戏读取外键灯光的 Mod
- `dist/` — 打包好的自检窗口 exe

外键 1–8：1P 对应键盘 `E D C X Z A Q W`，2P 对应小键盘 `8 9 6 3 2 1 4 7`。

## 双人（1P + 2P）

一台电脑插两台 iPad 就能双人拼机：

1. 第二台 iPad 打开 App，菜单第一行点「玩家 1P」切成「玩家 2P」。
   App 改听 24880 端口，电脑上的转发会自己找过去，不用填 UDID。
2. 双击 `pc/bridge.bat`（已带 `--serial COM33 --serial2 COM34 --usb`）。
3. 正常启动游戏，游戏会同时连上 COM3（1P）和 COM4（2P）。

- 两台 iPad 的 P1/P2 选择会记住，下次打开还是原来的机位；如果中途改了
  机位，重开一次 `bridge.bat` 让 USB 转发重新配对。
- 画面镜像：2P 时游戏左右分屏，软件按画面内容自动判断，左半给 1P、
  右半给 2P；如果认错了，加 `--video-layout single` 或 `--video-layout dual`。
- 只玩 1P：把 `bridge.bat` 里的 `--serial2 COM34` 删掉（或写成空字符串）。

另一个 32 区工程（Brokenithm-Evolved-iOS）与本工程无关。
