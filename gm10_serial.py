#!/usr/bin/env python3
"""GM10 模组串口读取/调试工具.

串口参数来自《400-GM10 模组评估板快速应用手册》:
    115200 波特率, 8 数据位, 1 停止位, 无校验, 无流控 (8N1)
    发送指令需带回车换行 (CRLF)
    模组上电 60s 后休眠, 首条 AT 仅唤醒, 需再发一条才有响应

用法:
    ./gm10_serial.py                 # 自动唤醒后交互监听(等待键盘输入指令)
    ./gm10_serial.py --wake-only     # 自动发 AT 唤醒并采集响应, 然后退出
    ./gm10_serial.py --cmd 'AT+CGSN' # 发一条指定指令并采集响应, 然后退出
    ./gm10_serial.py --baud 9600     # 覆盖波特率
"""
import argparse
import datetime
import os
import select
import sys
import termios
import time

PORT = "/dev/ttyUSB0"
BAUD = {115200: termios.B115200, 9600: termios.B9600, 4800: termios.B4800}


def open_port(port, baud):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = termios.IGNBRK | termios.IGNPAR          # iflag: 不做特殊处理
    a[1] = 0                                        # oflag: raw 输出
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag: 8N1
    a[3] = 0                                        # lflag: raw, 无回显
    a[4] = a[5] = BAUD.get(baud, termios.B115200)
    a[6][termios.VMIN] = 0
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def stamp():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def render(data, hexmode):
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    hx = " ".join(f"{b:02X}" for b in data)
    if hexmode:
        return f"HEX {hx}"
    return f"TXT {text!r}  HEX {hx}"


def drain(fd, seconds, hexmode, buf):
    """在给定秒数内读取并打印串口数据, buf 用于跨调用拼行."""
    end = time.time() + seconds
    got = 0
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
        if not r:
            break
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if not data:
            continue
        got += len(data)
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            print(f"[{stamp()}] RX {render(line, hexmode)}", flush=True)
    if buf:
        print(f"[{stamp()}] RX {render(buf, hexmode)}", flush=True)
        buf = b""
    return got, buf


def send(fd, cmd):
    payload = cmd.encode() + b"\r\n"
    os.write(fd, payload)
    print(f"[{stamp()}] TX {cmd!r} ({len(payload)} B)", flush=True)


def main():
    ap = argparse.ArgumentParser(description="GM10 串口读取工具")
    ap.add_argument("--baud", type=int, default=115200, help="波特率 (默认 115200)")
    ap.add_argument("--port", default=PORT, help="串口设备 (默认 /dev/ttyUSB0)")
    ap.add_argument("--hex", action="store_true", help="以十六进制显示")
    ap.add_argument("--wake-only", action="store_true", help="自动唤醒并采集后退出")
    ap.add_argument("--cmd", default=None, help="单条指令模式, 发送后采集并退出")
    ap.add_argument("--wait", type=float, default=3.0, help="采集等待秒数 (默认 3)")
    args = ap.parse_args()

    try:
        fd = open_port(args.port, args.baud)
    except PermissionError:
        print(f"无法打开 {args.port}: 权限不足。请将账户加入 dialout 组或用 ACL 授权。", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"无法打开 {args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"已打开 {args.port} @ {args.baud} 8N1 (hex显示={'开' if args.hex else '关'})", flush=True)
    buf = b""
    try:
        if args.cmd is not None:
            # 单条指令: 先唤醒再发指令
            send(fd, "AT")
            drain(fd, 1.0, args.hex, buf)
            send(fd, args.cmd)
            drain(fd, args.wait, args.hex, buf)
        elif args.wake_only:
            send(fd, "AT")            # 首条可能仅唤醒
            drain(fd, 0.8, args.hex, buf)
            send(fd, "AT")            # 再发一条以获取响应
            drain(fd, args.wait, args.hex, buf)
        else:
            # 交互模式
            print("输入指令回车发送(自动加CRLF); :hex 切换; :q 退出。提示: 休眠时先发 AT 唤醒再发指令。", flush=True)
            while True:
                r, _, _ = select.select([fd, sys.stdin], [], [], 0.2)
                if fd in r:
                    buf = drain(fd, 0.2, args.hex, buf)[1]
                if sys.stdin in r:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    line = line.rstrip("\r\n")
                    if line == ":q":
                        break
                    if line == ":hex":
                        args.hex = not args.hex
                        print(f"hex显示={'开' if args.hex else '关'}", flush=True)
                        continue
                    if line:
                        send(fd, line)
    finally:
        if buf:
            print(f"[{stamp()}] RX {render(buf, args.hex)}", flush=True)
        os.close(fd)
        print("串口已关闭", flush=True)


if __name__ == "__main__":
    main()
