# MaiPanel

把 iPad 当成 maimai DX 的触摸手台。

iPad 上是一整块 34 区圆盘，圆盘外侧一圈 8 个按键，圆盘上方是三角 / 刷卡 /
投币 / 菜单四个键（TEST、SERVICE、关闭和面板开关收在菜单里，关闭等于电脑
上的 Alt+F4）。触摸、按键和灯光
都走同一根 USB 线，由电脑上的桥接程序转成游戏的手台串口信号。外键灯会
同步回 iPad；声音留在电脑上，画面可以在菜单里打开镜像（调试用）。

- 不需要越狱，也不需要 Apple 开发者账号：云端构建的是未签名 IPA，自己
  侧载即可。
- 一台电脑可以同时接两台 iPad，按 1P / 2P 分屏拼机。
- 本仓库不包含游戏本体或任何游戏资源。

## 目录

- `app/` — iPad App（SwiftUI）
- `pc/` — 电脑侧桥接、USB 转发和自检窗口
- `pc/ledmod/` — 读取游戏外键灯光的 MelonLoader Mod（可选）

## iPad 侧

App 在 iPad 上是一个 TCP 服务端：1P 听 `24870`，2P 听 `24880`。电脑上的
桥接程序通过 USB 自动找到设备并连上去，不需要填 UDID 或 IP。

### 构建与安装

1. 在 GitHub 的 **Actions** 里运行 **Build unsigned IPA**（推送到
   `master` / `main` 也会自动构建）。
2. 跑完后在该次运行页面下载 **MaiPanel-unsigned** 产物，得到
   `MaiPanel-unsigned.ipa`。
3. 用 Sideloadly / AltStore 等工具，以你自己的 Apple ID 侧载到 iPad。
   免费账号签名的 App 7 天后过期，重新侧载即可。

### 双人（1P + 2P）

第二台 iPad 打开 App，菜单第一行点「玩家 1P」切成「玩家 2P」，App 改听
`24880`，电脑上的转发会自己找过去。

## 电脑侧

需要 Windows 10/11，并装好 Apple 设备驱动（iTunes 或 Apple Devices）。

1. 安装 Python 3.10+，然后：

   ```bat
   pip install -r pc\requirements.txt
   ```

2. 用 [com0com](https://sourceforge.net/projects/com0com/) 建两对虚拟串口：

   | 桥接程序用 | 游戏用 |
   |---|---|
   | COM33 | COM3（1P） |
   | COM34 | COM4（2P） |

   只玩 1P 时建第一对就够了。

3. 双击 `pc\bridge.bat`。它默认带 `--serial COM33 --serial2 COM34 --usb`；
   串口号不同就直接改这个文件。只玩 1P 时删掉 `--serial2 COM34`。
4. 启动游戏，游戏会连上 COM3（和 COM4）。

### 外键灯光（可选）

游戏默认不对第三方手台发灯光数据，需要装一个只读的 Mod：

1. 游戏目录装好 [MelonLoader](https://melonwiki.xyz/)（net35）。
2. 运行：

   ```bat
   pc\build_led_mod.bat "游戏目录"
   ```

   参数是包含 `Sinmai_Data` 的那一层文件夹。脚本会把
   `MaiPanelLed.dll` 编译进 `游戏目录\Mods\`。Mod 只读取外键灯光状态，
   通过本机 UDP `24872` 发给桥接程序，不修改游戏。

### 自检窗口

不启动游戏也能检查桥接和 iPad 是否连上：

```bat
python pc\panel_monitor.py
```

或者打包成 exe：

```bat
pc\build_exe.bat
```

产物是 `dist\MaiPanel.exe`。

## 外键映射

| 机位 | 外键 1–8 |
|---|---|
| 1P | `E D C X Z A Q W` |
| 2P | 小键盘 `8 9 6 3 2 1 4 7` |

## 画面镜像

在 iPad 的菜单里打开「画面」。电脑端按 60fps 抓取游戏窗口，裁成 9:16
推给 iPad；2P 时游戏左右分屏，会自动判断左半给 1P、右半给 2P。认错了可以
给桥接程序加 `--video-layout single` 或 `--video-layout dual`。

## 许可

[MIT](LICENSE)
