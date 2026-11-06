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

外键 1–8 对应键盘 `E D C X Z A Q W`。

另一个 32 区工程（Brokenithm-Evolved-iOS）与本工程无关。
