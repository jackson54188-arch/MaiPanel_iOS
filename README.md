# MaiPanel — iPad 虚拟手台（maimai DX）

独立工程，和 Brokenithm / BeLikeithm 无关。iPad 上跑 34 区触摸面板，
画面输出留在电脑上（本阶段无视频串流，声音也仍然走电脑）。

```
iPad (MaiPanel.app)                      电脑
┌────────────────────┐   USB (usbmux)   ┌──────────────────────────────┐
│ TCP 服务 :24870    │ ───────────────▶ │ bridge.py 连 127.0.0.1:24870  │
│ 34 区命中 + 位掩码 │                  │   ↓ 转成手台串口协议           │
└────────────────────┘                  │ COM33 ──(com0com)── COM3     │
                                        │   ↓                          │
                                        │ Sinmai.exe (maimai DX 1.60)  │
                                        └──────────────────────────────┘
```

## 目录

- `app/` — iOS 工程源码（XcodeGen 描述，无签名，无个人信息）
- `pc/bridge.py` — 电脑侧桥接：TCP ⇄ 虚拟串口，顺带广播状态给监视窗口
- `pc/forward.py` — usbmux 转发的常驻版：自动挑 iPad、断线重连、中文报错
- `pc/bridge.bat` — 双击启动桥接（自动拉起 usbmux 转发）
- `pc/panel_monitor.py` — 电脑端自检窗口：iPad 按哪块，哪块就亮
- `dist/MaiPanel.exe` — 自检窗口打包版，双击即用，不需要装 Python
- `pc/build_exe.bat` — 重新打包上面那个 exe
- `pc/sim_app.py` — 假 iPad，没有真机时用来验证桥接和协议
- `game/touch_probe.py` — 只发触摸包的串口探针（在 `D:\SEGA\Maimai\tools\`）
- `game/install_com0com.bat` — 装 com0com 并建 COM3↔COM33（需要管理员）

## 一次性准备（电脑，需要管理员）

双击 `D:\SEGA\Maimai\tools\install_com0com.bat` → UAC 点「是」。

它会：装 com0com 3.0.0.0 → 建 `COM3`(游戏侧) ↔ `COM33`(工具侧) → 打开缓冲模拟。
日志：`D:\SEGA\Maimai\tools\com0com_setup.log`。

然后把 `D:\SEGA\Maimai\Package\mai2.ini` 里的 `DummyTouchPanel=1` 改成 `0`。

## 构建 IPA（免费，不签名）

1. 新建一个**私有** GitHub 仓库，把 `maipanel/` 整个推送上去；
2. Actions → `Build unsigned IPA` → Run workflow；
3. 下载 artifact `MaiPanel-unsigned`，用侧载工具装到 iPad。

## 运行顺序

```
:: 1. 电脑：双击 pc\bridge.bat（自动拉起 usbmux 转发）
::    等价于 python pc\bridge.py --serial COM33 --usb

:: 2. iPad：打开 MaiPanel，状态显示「PC 已连接」

:: 3. 电脑：启动游戏
D:\SEGA\Maimai\启动.bat
```

## 外键（1–8）

iPad 圆盘外侧一圈是 8 个外键，按下后经同一条 USB 连接发给电脑，
由 `bridge.py` 用 `SendInput` 注入键盘，等效于按物理键盘：

```
外键 1 2 3 4 5 6 7 8
键盘 E D C X Z A Q W
```

这套映射已经写进 `segatools.ini` 的 `1p_btn1..8` 和
`AquaMai.toml` 的 `Button1..8_1P`，所以游戏里就是 1P 的 1–8 号键。
按键发到当前前台窗口，玩的时候让游戏在前台即可；iPad 上按住不放
电脑也保持按住，多键同时按（和弦）同样支持。

App 左上角状态里会显示 `外键: 1 3` 这样的实时状态；
`sim_app.py` 也会把 1–8 逐个按一遍，方便没有 iPad 时验证。

外观是贴着圆盘外缘的弧形方块：内外两条边是圆弧、两条侧边是径向直线，
边长约为 D 区宽度的 1.1 倍（0.23R），上下底几乎等长，看起来接近正方形。
按键之间的空隙也用同样厚度、颜色更暗的弧片填满（整圈 16 块），
和两侧按键各留 2° 缝，且不响应触摸。
白色半透明磨砂质感，按住时变亮并带一圈柔光；右上角的「灯光」按钮可一键
关掉底色和光晕（只留描边），不影响按键功能。
这里的灯光是 App 自己的按键灯效，**不跟随游戏 RGB LED**：游戏的
LED 数据由 mai2hook 在进程内模拟，不经过触摸串口，所以要让 App
同步游戏灯效需要另做一层拦截（尚未实现）。

外键和 34 区由**同一个触摸视图**统一判定：手指从外键滑进圆盘、
或者从圆盘滑到外键，都会实时切换成对应输入，不会因为某个 UIView
一直抓着触点而丢失。按在缝里的装饰弧片上则当作没按。

## iPad 上的机台功能键

左下角一排 `TEST` / `SERVICE` / `投币` / `△ 三角`，点一下经同一条
USB 链路发给 `bridge.py`，由它用 `SendInput` 点按一次：

| iPad 按钮 | 注入按键 | 游戏侧配置 |
| --- | --- | --- |
| TEST | `1` | segatools `[io4] test=0x31`、AquaMai `Test = "Alpha1"` |
| SERVICE | `8` | segatools `[io4] service=0x38` |
| 投币 | `3` | segatools `[io4] coin=0x33` |
| △ 三角 | `2` | AquaMai `Select_1P = "Alpha2"` |

它们只在 App 连上电脑时发送，按键打到当前前台窗口，所以点之前
先让游戏保持前台。电脑端自检窗口底部同样保留了一排，两个入口共用
bridge 的 `injector.tap`。

## 界面布局

App 固定竖屏，底部是触摸圆盘，顶部留出一条黑色「姓名框」区域
（屏幕高度的 17%，最少 84 pt），和街机的竖屏比例一致；以后做视频
串流时这块就是游戏上方的姓名/曲目区。触摸区本身不铺任何底色或
分区色块，只有可选的区域编号和手指圆点，「灯光」开关只影响圆盘
外壳的那一圈按键。

## 电脑端自检窗口

游戏没开的时候也能看 iPad 有没有按对。直接双击 `dist\MaiPanel.exe`，
或者从源码跑：

```
python pc\panel_monitor.py            :: 连 bridge.py，实时亮区
python pc\panel_monitor.py --demo     :: 不连 bridge，自动轮流点亮 34 区
```

界面是黑底白字的一个大圆盘：深灰分区 = 未按，纯白 + 光晕 = 正在按，
激活区的编号变黑。下方是连接状态和当前掩码，没有任何弹窗。

窗口底部还有一排机台功能键：`TEST`、`SERVICE`、`投币`、`△ 三角`。
点一下会先把 Sinmai 切回前台，再让桥接按一次对应的键（约 90 ms）：

| 按钮 | 注入按键 | 游戏侧配置 |
| --- | --- | --- |
| TEST | `1` | segatools `[io4] test=0x31`、AquaMai `Test = "Alpha1"` |
| SERVICE | `8` | segatools `[io4] service=0x38` |
| 投币 | `3` | segatools `[io4] coin=0x33` |
| △ 三角 | `2` | AquaMai `Select_1P = "Alpha2"` |

按键会打到前台窗口，所以点完按钮焦点会回到游戏；找不到 Sinmai.exe
时按钮照样发命令，但状态栏会提示可能没有焦点。

三行状态的含义：

- `桥接程序` — bridge.py 在跑，并且监视端口连上了。
- `游戏串口` — 游戏已经握手并接受了这块手台。
- `iPad 面板` — 只有收到 iPad 的真实数据帧才算「已连接」。
  **注意：本地转发端口即使没接 iPad 也会接受 TCP 连接，所以「端口能
  连上」不证明 iPad 在线。** 出现
  `无数据（检查 USB 转发）` 时，说明转发端口通了但 iPad 没回数据：
  检查 USB 线、解锁 iPad、并让 App 保持在前台。

窗口按 Esc 关闭，不影响桥接和游戏。bridge.py 默认在 `127.0.0.1:24871`
广播状态；不想开就加 `--hub-port 0`。这条连接是双向的：bridge 往下发
状态 JSON，监视窗口往上发 `{"cmd": "tap", "key": "coin"}`
（key 取 `test` / `service` / `coin` / `select`）。

iPad 上一直提示「PC 未连接」时：

```
python -m pymobiledevice3 usbmux list        :: 应能看到 iPad 的 UDID
```

- 列表为空 → 线/端口/信任问题。C-to-C 线必须是能传数据的（很多
  充电线只带 VBUS/GND，插上只能充电）；插好后在 iPad 上点「信任」；
- 列表有设备，但 App 还是未连接 → 让 App 留在前台（后台会被挂起）；
- 有多台设备/模拟器时用 `python pc\bridge.py --serial COM33 --usb --udid <UDID>`
  指定用哪台 iPad。

转发现在由 `pc/forward.py` 在 bridge 进程内做，不再调
`pymobiledevice3 usbmux forward` 命令行：

- 启动时直接挑「USB 连接的那台 iPad」，多台时先试连它的端口，
  不再对着第一台设备盲连；
- 断线后每 1 秒重连，iPad 重新插上会自动恢复；
- 原始库的 `failed to connect to remote endpoint` 日志被压掉了，
  只在中文字幕里说明原因（没接 iPad / 充电线 / 未解锁未信任 /
  usbmuxd 没起来）。看到这些提示说明连接这一层没通，和 App 无关。

重新打包 exe：

```
pc\build_exe.bat
```

只有一个 exe，首次运行若被 SmartScreen 拦，点「更多信息 → 仍要运行」。

没有真机时，先用假 iPad 验证协议这一半：

```
python pc\sim_app.py          :: 终端 A
python pc\bridge.py --serial none   :: 终端 B，会把触摸包打印成 SERIAL 28 ...
python pc\panel_monitor.py    :: 终端 C，看到 34 区轮流亮
```

## 协议

发往电脑的帧（小端）：

```
"MPNL" | version:u8 | type:u8 | length:u16 | payload
```

- `type 0x01` 触摸：payload = 34 位区域掩码，5 字节小端
  （位序 A1..A8, B1..B8, C1, C2, D1..D8, E1..E8）
- `type 0x04` 外键：payload = 1 字节，bit0..bit7 = 外键 1..8
- `type 0x05` 机台功能键：payload = 1 字节，1=TEST、2=SERVICE、3=投币、4=三角
- `type 0x02/0x03` ping / pong
- `type 0x10` 视频（后续阶段）

桥接把掩码打包成手台串口包：

```
0x28 | 7 字节 | 0x29        每字节装 5 个连续区域，低位在前
```

握手：游戏发 6 字节，第 4 字节是 `'r'`/`'k'` 时回
`0x28 d1 d2 d3 d4 0x29`，`'A'` 表示已被接受，`'L'`/`'E'` 表示断开。

## 命中判定

`app/Sources/Resources/panel_mask.png` 是一张 1456×1456 的分区颜色图，
每个区域一种唯一 RGB（取自 maimai-windows-touch-panel 的实测布局）。
手指坐标直接查这张图，不需要手写几何，异形区域也准确。

App 里「范围」用来调采样半径（小 / 中 / 大）：iPad 坐标本身很准，
默认「中」就能用；如果发现容易蹭到隔壁区就往「小」调。

## 还没做（下一阶段）

- 游戏画面串流：窗口化 + DXGI 抓屏 + QSV 编码，走同一条 USB TCP
- 音频不串流，继续用电脑扬声器
