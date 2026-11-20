#!/usr/bin/env python3
"""GM13 卫星模组实时仪表盘.

自动轮询 AT 命令手册中的全部只读查询指令, 实时显示:
  - 卫星时间 (AT+CCLK?, UTC) 与北京时间
  - 卫星信号质量 (AT+CESQ?, SNR) 分级进度条
  - 卫星连接状态 (AT+CREG? 的 state 位)
  - 其余全部可查数据 (信号/定位/配置/存储/身份)

串口在独立线程操作, 主线程只跑 Tk 界面。模组休眠时每轮先发 AT 唤醒。
"""
import datetime
import os
import queue
import re
import select
import termios
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

PORT = "/dev/ttyUSB0"
BAUDRATE = termios.B115200

# (命令, 显示名, 响应前缀)  前缀为 None 表示取全部行
QUERIES = [
    ("AT+CCLK?", "卫星时间 (UTC)", "+CCLK:"),
    ("AT+CESQ?", "卫星信号质量 SNR", "+CESQ:"),
    ("AT+CSQ?",  "环境噪声", "+CSQ:"),
    ("AT+CREG?", "联网状态 (mode,state)", "+CREG:"),
    ("AT+CGPS?", "定位 (纬度,方向,经度,方向,高程)", "+CGPS:"),
    ("AT+CMMQ?", "待发数据帧数", "+CMMQ:"),
    ("AT+READ?", "下行数据帧数", "+READ:"),
    ("AT+CFG?",  "配置汇总", None),
    ("AT+CGBR?", "串口波特率", "+CGBR:"),
    ("AT+CPSM?", "休眠模式", "+CPSM:"),
    ("AT+SVMD?", "存储满处置", "+SVMD:"),
    ("AT+ROMD?", "下行输出方式", "+ROMD:"),
    ("AT+CLOG?", "通讯 LOG", "+CLOG:"),
    ("AT+LBTM?", "位置采集间隔 (s)", "+LBTM:"),
    ("AT+CGMI?", "制造商", "+CGMI:"),
    ("AT+CGMM?", "型号", "+CGMM:"),
    ("AT+CGMR?", "硬件/软件版本", "+CGMR:"),
    ("AT+CGSN?", "序列号", "+CGSN:"),
]


class SerialAT:
    """独占串口; 每轮 wake() 一次后连续 query() 多条指令."""

    def __init__(self, port=PORT):
        self.port = port
        self.fd = None
        self.lock = threading.Lock()

    def open(self):
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(fd)
        a[0] = termios.IGNBRK | termios.IGNPAR
        a[1] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[3] = 0
        a[4] = a[5] = BAUDRATE
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        termios.tcflush(fd, termios.TCIOFLUSH)
        self.fd = fd

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def _write(self, cmd):
        os.write(self.fd, cmd.encode("ascii") + b"\r\n")

    def _read(self, seconds):
        buf = b""
        end = time.time() + seconds
        while True:
            remain = end - time.time()
            if remain <= 0:
                break
            r, _, _ = select.select([self.fd], [], [], remain)
            if not r:
                break
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                break
            buf += chunk
            low = buf.lower()
            if b"ok\r" in low or b"error\r" in low or b"error:" in low:
                break
        return buf

    def wake(self):
        """唤醒休眠模组, 丢弃响应."""
        with self.lock:
            if self.fd is None:
                self.open()
            self._write("AT")
            self._read(0.25)
            termios.tcflush(self.fd, termios.TCIFLUSH)

    def query(self, cmd, timeout=0.8):
        with self.lock:
            if self.fd is None:
                self.open()
            self._write(cmd)
            raw = self._read(timeout)
        text = raw.decode("ascii", "replace")
        lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
        return lines


def find_value(lines, prefix):
    for ln in lines:
        if ln.upper().startswith(prefix.upper()):
            return ln[len(prefix):].strip()
    return None


def has_error(lines):
    return any("ERROR" in ln.upper() for ln in lines)


def parse_cclk(s):
    m = re.match(r"(\d{4})/(\d{2})/(\d{2}),(\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x) for x in m.groups()])
    except ValueError:
        return None


def signal_info(snr):
    if snr is None or snr == -128:
        return "无信号", "未收到卫星信号", "#7f849c", 0.0
    ratio = max(0.0, min(1.0, (snr + 15) / 30.0))
    if snr < -10:
        level, color = "很差", "#e74c3c"
    elif snr <= -6:
        level, color = "一般", "#f39c12"
    elif snr <= 0:
        level, color = "较好", "#2ecc71"
    else:
        level, color = "非常好", "#27ae60"
    return f"{snr:+d} dB", level, color, ratio


def worker(stop, q, interval_ref):
    ser = SerialAT()
    while not stop.is_set():
        stamp = time.strftime("%H:%M:%S")
        try:
            ser.wake()
            data = {}
            for cmd, label, prefix in QUERIES:
                lines = ser.query(cmd)
                if has_error(lines):
                    val = "不支持/错误: " + " ".join(lines)
                elif prefix is None:
                    val = "  ".join(lines) or "空"
                else:
                    val = find_value(lines, prefix)
                    if val is None:
                        val = "无响应"
                    else:
                        val = val.replace("\r", " ").strip()
                data[label] = val
            q.put(("data", {"stamp": stamp, "data": data}))
        except Exception as e:
            try:
                ser.close()
            except Exception:
                pass
            q.put(("error", f"{stamp} 读取失败: {e}"))
        stop.wait(interval_ref[0])
    ser.close()


class App:
    BG = "#16161e"
    CARD = "#1f1f2b"
    FG = "#e6e6f0"
    MUTED = "#9aa0b4"

    def __init__(self, root):
        self.root = root
        root.title("GM13 卫星模组实时仪表盘")
        root.geometry("760x820+120+80")
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.stop = threading.Event()
        self.q = queue.Queue()
        self.interval = [2.0]
        self.build()
        threading.Thread(target=worker, args=(self.stop, self.q, self.interval), daemon=True).start()
        root.after(150, self.poll)

    def card(self, parent):
        f = tk.Frame(parent, bg=self.CARD, padx=16, pady=12)
        f.pack(fill="x", padx=16, pady=6)
        return f

    def build(self):
        big = tkfont.Font(size=30, weight="bold")
        mid = tkfont.Font(size=16, weight="bold")
        small = tkfont.Font(size=10)
        label = tkfont.Font(size=11)

        # 标题栏
        head = tk.Frame(self.root, bg=self.BG)
        head.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(head, text="GM13 卫星模组实时仪表盘", font=mid, bg=self.BG, fg=self.FG).pack(side="left")
        self.status = tk.Label(head, text="● 连接中…", font=small, bg=self.BG, fg="#f39c12")
        self.status.pack(side="right")
        self.lastup = tk.Label(self.root, text="更新: --", font=small, bg=self.BG, fg=self.MUTED)
        self.lastup.pack(anchor="e", padx=16)

        # 三项核心指标并排
        core = tk.Frame(self.root, bg=self.BG)
        core.pack(fill="x", padx=16, pady=6)

        c1 = tk.Frame(core, bg=self.CARD, padx=14, pady=10, width=360)
        c1.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(c1, text="卫星时间 (UTC)", font=label, bg=self.CARD, fg=self.MUTED).pack(anchor="w")
        self.time_big = tk.Label(c1, text="----/--/-- --:--:--", font=tkfont.Font(size=19, weight="bold"), bg=self.CARD, fg=self.FG)
        self.time_big.pack(anchor="w", pady=(4, 0))
        self.time_bj = tk.Label(c1, text="北京时间: --", font=small, bg=self.CARD, fg=self.MUTED)
        self.time_bj.pack(anchor="w", pady=(2, 0))

        c2 = tk.Frame(core, bg=self.CARD, padx=14, pady=10, width=360)
        c2.pack(side="left", fill="both", expand=True, padx=(6, 0))
        tk.Label(c2, text="卫星信号质量 (SNR)", font=label, bg=self.CARD, fg=self.MUTED).pack(anchor="w")
        self.sig_canvas = tk.Canvas(c2, width=330, height=26, bg=self.CARD, highlightthickness=0)
        self.sig_canvas.pack(pady=(8, 0))
        self.level_lbl = tk.Label(c2, text="等级: --", font=small, bg=self.CARD, fg=self.MUTED)
        self.level_lbl.pack(anchor="w", pady=(4, 0))

        # 卫星连接状态横幅
        conn = tk.Frame(self.root, bg=self.CARD, padx=16, pady=10)
        conn.pack(fill="x", padx=16, pady=6)
        tk.Label(conn, text="卫星连接状态", font=label, bg=self.CARD, fg=self.MUTED).pack(side="left")
        self.conn_lbl = tk.Label(conn, text="● 查询中…", font=mid, bg=self.CARD, fg="#f39c12")
        self.conn_lbl.pack(side="left", padx=(16, 0))
        self.creg_lbl = tk.Label(conn, text="", font=small, bg=self.CARD, fg=self.MUTED)
        self.creg_lbl.pack(side="right")

        # 全部数据表
        tbl = tk.Frame(self.root, bg=self.CARD, padx=12, pady=10)
        tbl.pack(fill="both", expand=True, padx=16, pady=6)
        tk.Label(tbl, text="全部实时数据 (自动刷新)", font=label, bg=self.CARD, fg=self.MUTED).pack(anchor="w", pady=(0, 6))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#1f1f2b", fieldbackground="#1f1f2b",
                        foreground=self.FG, rowheight=24, font=tkfont.Font(size=10))
        style.configure("Treeview.Heading", background="#2a2a3a", foreground=self.FG,
                        font=tkfont.Font(size=10, weight="bold"))
        style.map("Treeview", background=[("selected", "#3a3a55")])
        cols = ("item", "value", "cmd")
        self.tree = ttk.Treeview(tbl, columns=cols, show="headings", height=14)
        self.tree.heading("item", text="数据项")
        self.tree.heading("value", text="数值")
        self.tree.heading("cmd", text="指令")
        self.tree.column("item", width=210, anchor="w")
        self.tree.column("value", width=340, anchor="w")
        self.tree.column("cmd", width=120, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.rows = {}

        # 底部控制
        foot = tk.Frame(self.root, bg=self.BG)
        foot.pack(fill="x", padx=16, pady=(4, 12))
        tk.Label(foot, text="刷新间隔:", font=small, bg=self.BG, fg=self.MUTED).pack(side="left")
        self.interval_var = tk.StringVar(value="2")
        tk.OptionMenu(foot, self.interval_var, "1", "2", "5", command=self.set_interval).pack(side="left", padx=(6, 12))
        self.errlbl = tk.Label(foot, text="", font=small, bg=self.BG, fg="#e74c3c")
        self.errlbl.pack(side="left")

    def set_interval(self, val):
        try:
            self.interval[0] = float(val)
        except ValueError:
            pass

    def draw_signal(self, snr):
        c = self.sig_canvas
        c.delete("all")
        w, h = 330, 26
        val, level, color, ratio = signal_info(snr)
        c.create_rectangle(0, 5, w, h - 5, fill="#33333f", outline="")
        if ratio > 0:
            c.create_rectangle(0, 5, max(10, w * ratio), h - 5, fill=color, outline="")
        c.create_text(8, h // 2, text=val, anchor="w", fill="#fff", font=tkfont.Font(size=10))
        return level, color

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "error":
                    self.status.config(text="● 读取失败", fg="#e74c3c")
                    self.errlbl.config(text=payload)
                    continue
                self.status.config(text="● 已连接", fg="#2ecc71")
                self.errlbl.config(text="")
                d = payload["data"]
                self.lastup.config(text=f"更新: {payload['stamp']}")
                # 时间
                cclk = d.get("卫星时间 (UTC)", "--")
                self.time_big.config(text=cclk)
                dt = parse_cclk(cclk)
                if dt:
                    bj = dt + datetime.timedelta(hours=8)
                    self.time_bj.config(text=f"北京时间 (UTC+8): {bj:%Y/%m/%d %H:%M:%S}")
                # 信号
                cesq = d.get("卫星信号质量 SNR", "")
                m = re.search(r"-?\d+", cesq)
                snr = int(m.group()) if m else None
                level, color = self.draw_signal(snr)
                self.level_lbl.config(text=f"等级: {level}   原始: {cesq}", fg=color)
                # 连接状态
                creg = d.get("联网状态 (mode,state)", "")
                parts = [p.strip() for p in creg.split(",")]
                state = parts[1] if len(parts) > 1 else None
                if state == "1":
                    self.conn_lbl.config(text="● 已连接卫星", fg="#2ecc71")
                elif state == "0":
                    self.conn_lbl.config(text="● 未连接卫星", fg="#e74c3c")
                else:
                    self.conn_lbl.config(text="● 状态未知", fg="#f39c12")
                self.creg_lbl.config(text=f"CREG: {creg}   环境噪声: {d.get('环境噪声','--')}")
                # 全部数据表
                for cmd, label, _ in QUERIES:
                    val = d.get(label, "--")
                    if label not in self.rows:
                        iid = self.tree.insert("", "end", values=(label, val, cmd))
                        self.rows[label] = iid
                    else:
                        self.tree.item(self.rows[label], values=(label, val, cmd))
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    def on_close(self):
        self.stop.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
