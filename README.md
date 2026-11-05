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
- `pc/bridge.py` — 电脑侧桥接：TCP ⇄ 虚拟串口
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
:: 1. 电脑：启动桥接（自动拉起 usbmux 转发）
python pc\bridge.py --serial COM33 --usb

:: 2. iPad：打开 MaiPanel，状态显示「PC 已连接」

:: 3. 电脑：启动游戏
D:\SEGA\Maimai\启动.bat
```

没有真机时，先用假 iPad 验证协议这一半：

```
python pc\sim_app.py          :: 终端 A
python pc\bridge.py --serial none   :: 终端 B，会把触摸包打印成 SERIAL 28 ...
```

## 协议

发往电脑的帧（小端）：

```
"MPNL" | version:u8 | type:u8 | length:u16 | payload
```

- `type 0x01` 触摸：payload = 34 位区域掩码，5 字节小端
  （位序 A1..A8, B1..B8, C1, C2, D1..D8, E1..E8）
- `type 0x02/0x03` ping / pong
- `type 0x10` 视频（后续阶段）

桥接把掩码打包成手台串口包：

```
0x28 | 7 字节 | 0x29        每字节装 5 个连续区域，低位在前
```

握手：游戏发 6 字节，第 4 字节是 `'r'`/`'k'` 时回
`0x28 d1 d2 d3 d4 0x29`，`'A'` 表示已被接受，`'L'`/`'E'` 表示断开。

## 命中判定

`app/Sources/Resources/panel_mask.png` 是一张 1360×1360 的分区颜色图，
每个区域一种唯一 RGB（取自 maimai-windows-touch-panel 的实测布局）。
手指坐标直接查这张图，不需要手写几何，异形区域也准确。

App 里「范围」用来调采样半径（小 / 中 / 大）：iPad 坐标本身很准，
默认「中」就能用；如果发现容易蹭到隔壁区就往「小」调。

## 还没做（下一阶段）

- 游戏画面串流：窗口化 + DXGI 抓屏 + QSV 编码，走同一条 USB TCP
- 音频不串流，继续用电脑扬声器
