# satellite-modem-monitor

龙芯 loongarch64 本地读取与监控 **240-GM13 卫星物联网模组**（天启星座短报文）的小工具集。

模组通过 USB-TTL 转换器接在 `/dev/ttyUSB0`，通信参数 **115200 8N1**，协议为厂商自定义 AT 指令集。本项目用 Python 标准库（`termios` + `tkinter`）实现，无第三方 Python 依赖。

## 功能

- `gm10_serial.py` —— 命令行串口工具：唤醒模组、发送 AT 指令、实时显示返回（文本/十六进制）
- `gm13_monitor.py` —— Tkinter 实时仪表盘：自动轮询全部 18 条只读指令，显示卫星时间、信号质量、连接状态和其余设备数据，2 秒刷新（可选 1/2/5 秒）

仪表盘界面包含：

- 卫星时间（UTC）与北京时间换算
- 卫星信号质量 SNR 分级进度条（未收到信号 / 很差 / 一般 / 较好 / 非常好）
- 卫星连接状态（`AT+CREG?` 的 state 位：已连接 / 未连接）
- 全部实时数据表：信号、噪声、定位、待发/下行帧数、配置、身份信息等

## 硬件与接线

- 模组：240-GM13（LCC 封装），AT 串口为 `AT_RX`(8 脚) / `AT_TX`(9 脚)，TTL 3.3V 电平
- 供电：+3.3~5.3V；卫星通信测试必须用锂电池并接 400M 高增益天线，天线距电路板 ≥1.5m
- 本机：龙芯 3B6000M（loongarch64）+ AOSC OS 14，USB-TTL 设备节点 `/dev/ttyUSB0`

## 串口权限

`/dev/ttyUSB0` 属 `root:dialout`，普通账户需授权其一：

```bash
# 临时（重启或 USB 重插后失效）
sudo setfacl -m u:$USER:rw /dev/ttyUSB0

# 永久（重新登录生效）
sudo usermod -aG dialout $USER
```

USB 重插会让 udev 重建设备节点、冲掉 ACL，此时重新执行 `setfacl` 即可。

## 用法

命令行工具：

```bash
python3 gm10_serial.py --wake-only            # 自动唤醒并采集后退出
python3 gm10_serial.py --cmd 'AT+CGPS?'       # 发单条指令并采集
python3 gm10_serial.py                        # 交互模式，:hex 切换，:q 退出
```

实时仪表盘：

```bash
python3 gm13_monitor.py
```

systemd user 服务（可选，后台常驻 + 开机自启）：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/gm13-monitor.service ~/.config/systemd/user/
# 按实际会话修改 Environment=DISPLAY / XAUTHORITY
systemctl --user daemon-reload
systemctl --user enable --now gm13-monitor.service
```

## 关键注意点

- 模组在非通讯状态 60 秒无输入会休眠，首条 `AT` 只唤醒、不响应，需再发一条才有效。两个脚本每轮都先发 `AT` 唤醒。
- 扩展类指令必须用 `?` 查询（如 `AT+CGSN?`），只发 `AT+CGSN` 会返回 `+CME ERROR:21`（不支持此种操作）。
- 指令一次只执行一条，上条完成后才可发下条。
- 信号质量 `AT+CESQ?`：正常 -15~+15，-128 表示未收到卫星信号；室内无天线时为 -128 属正常。
- 连接状态 `AT+CREG?`：`+CREG:<mode>,<state>`，state=1 表示已连接卫星。

## 常用 AT 指令

| 指令 | 用途 |
|------|------|
| `AT` | 唤醒/测试 |
| `AT+CCLK?` | 卫星时间 |
| `AT+CESQ?` | 卫星信号质量 SNR |
| `AT+CSQ?` | 环境噪声 |
| `AT+CREG?` | 联网状态 |
| `AT+CGPS?` | 定位（经纬度/高程） |
| `AT+CMMQ?` | 待发数据帧数 |
| `AT+READ?` | 下行数据帧数 |
| `AT+CFG?` | 全部配置 |
| `AT+CGBR?` | 串口波特率 |
| `AT+CGMI?` / `AT+CGMM?` / `AT+CGMR?` / `AT+CGSN?` | 制造商 / 型号 / 版本 / 序列号 |

完整指令集见厂商《终端模组 AT 命令手册 V2.4》，硬件参数见《240-GM13 模组规格书 V1.1》（均为厂商版权文档，未随本仓库分发）。

## 许可

MIT
