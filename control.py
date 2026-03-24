#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import threading
import time
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import messagebox
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False


# =========================================================
# Global settings
# =========================================================
PMX_IP = "192.168.1.10"
PMX_PORT = 5025
SOCKET_TIMEOUT = 3.0

USE_GUI = True          # True: GUI, False: CUI
ENABLE_LOG = False      # True: start logging automatically in CUI / GUI button available
TARGET_VOLTAGE = 10.0   # desired voltage [V]

MAX_VOLTAGE = 62.0      # [V]
MAX_CURRENT = 0.010     # [A] = 10 mA

RAMP_STEP_V = 1.0       # [V]
RAMP_INTERVAL_S = 0.1   # [s]

LOG_INTERVAL_S = 10.0   # [s]
POLL_INTERVAL_MS = 1000 # GUI measurement refresh

# 拡張子は指定どおり .cvs
LOG_EXTENSION = ".cvs"


# =========================================================
# PMX controller
# =========================================================
class PMXController:
    def __init__(self, host=PMX_IP, port=PMX_PORT, timeout=SOCKET_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.lock = threading.Lock()

    def connect(self):
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def write(self, cmd: str):
        with self.lock:
            self.connect()
            self.sock.sendall((cmd.strip() + "\n").encode("ascii"))

    def query(self, cmd: str) -> str:
        with self.lock:
            self.connect()
            self.sock.sendall((cmd.strip() + "\n").encode("ascii"))
            data = b""
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            return data.decode("ascii", errors="replace").strip()

    def initialize(self):
        # 外部制御無効、電流制限設定、保護解除、初期電圧0V
        self.write("VOLT:EXT:SOUR NONE")
        self.write("CURR:EXT:SOUR NONE")
        self.write(f"CURR {MAX_CURRENT:.6f}")
        self.write("OUTP:PROT:CLE")
        self.write("VOLT 0.000000")

    def output_on(self):
        # ON時は必ず0V固定
        self.write("VOLT 0.000000")
        self.write(f"CURR {MAX_CURRENT:.6f}")
        self.write("OUTP:PROT:CLE")
        self.write("OUTP 1")

    def output_off(self):
        self.write("OUTP 0")

    def set_voltage(self, v: float):
        if v < 0:
            v = 0
        if v > MAX_VOLTAGE:
            v = MAX_VOLTAGE
        self.write(f"VOLT {v:.6f}")

    def measure_voltage(self) -> float:
        return float(self.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        return float(self.query("MEAS:CURR?"))

    def get_output_state(self) -> bool:
        ans = self.query("OUTP?")
        return ans.strip() in ("1", "ON")

    def get_set_voltage(self) -> float:
        try:
            return float(self.query("VOLT?"))
        except Exception:
            return 0.0


# =========================================================
# Logger
# =========================================================
class PMXLogger:
    def __init__(self, controller: PMXController):
        self.ctrl = controller
        self.running = False
        self.thread = None
        self.filename = None

    def _make_filename(self):
        # yyyy-mmdd-HHMMSS.cvs
        return datetime.now().strftime("%Y-%m%d-%H%M%S") + LOG_EXTENSION

    def start(self):
        if self.running:
            return
        self.filename = self._make_filename()
        with open(self.filename, "w", encoding="utf-8") as f:
            f.write("timestamp,voltage_V,current_A,output_on\n")
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _worker(self):
        while self.running:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                v = self.ctrl.measure_voltage()
                i = self.ctrl.measure_current()
                on = int(self.ctrl.get_output_state())
                line = f"{ts},{v:.6f},{i:.6f},{on}\n"
            except Exception as e:
                line = f"{ts},ERROR,{str(e).replace(',', ';')},-1\n"

            try:
                with open(self.filename, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

            for _ in range(int(LOG_INTERVAL_S * 10)):
                if not self.running:
                    break
                time.sleep(0.1)


# =========================================================
# Ramp utility
# =========================================================
def ramp_voltage(ctrl: PMXController, target_v: float, log_func=print):
    if target_v < 0:
        target_v = 0
    if target_v > MAX_VOLTAGE:
        target_v = MAX_VOLTAGE

    current_set = ctrl.get_set_voltage()

    # 端数のある最終値まで安全に到達
    if abs(target_v - current_set) < 1e-9:
        return

    step = RAMP_STEP_V if target_v > current_set else -RAMP_STEP_V
    v = current_set

    while True:
        next_v = v + step

        if (step > 0 and next_v >= target_v) or (step < 0 and next_v <= target_v):
            ctrl.set_voltage(target_v)
            log_func(f"Set voltage -> {target_v:.1f} V")
            break
        else:
            ctrl.set_voltage(next_v)
            log_func(f"Set voltage -> {next_v:.1f} V")
            v = next_v
            time.sleep(RAMP_INTERVAL_S)


# =========================================================
# GUI
# =========================================================
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("PMX250-0.25A Controller")

        self.ctrl = PMXController()
        self.logger = PMXLogger(self.ctrl)
        self.ramp_thread = None

        try:
            self.ctrl.initialize()
        except Exception as e:
            messagebox.showwarning("Warning", f"Initialization failed:\n{e}")

        self.target_var = tk.StringVar(value=str(TARGET_VOLTAGE))
        self.meas_v_var = tk.StringVar(value="--")
        self.meas_i_var = tk.StringVar(value="--")
        self.log_status_var = tk.StringVar(value="LOG: OFF")

        self.build_widgets()
        self.update_ui_state()
        self.update_measurements()

    def build_widgets(self):
        pad = {"padx": 8, "pady": 6}

        tk.Label(self.root, text="設定電圧 [V]").grid(row=0, column=0, sticky="e", **pad)
        self.entry_target = tk.Entry(self.root, textvariable=self.target_var, width=12)
        self.entry_target.grid(row=0, column=1, **pad)

        self.btn_set = tk.Button(self.root, text="電圧セット", width=14, command=self.on_set_voltage)
        self.btn_set.grid(row=0, column=2, **pad)

        self.btn_onoff = tk.Button(self.root, text="OFF", width=14, bg="light gray", command=self.on_toggle_output)
        self.btn_onoff.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)

        self.btn_log = tk.Button(self.root, text="ログ開始", width=14, command=self.on_toggle_log)
        self.btn_log.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)

        tk.Label(self.root, text="現在の電圧 [V]").grid(row=3, column=0, sticky="e", **pad)
        tk.Label(self.root, textvariable=self.meas_v_var, width=16, anchor="w").grid(row=3, column=1, columnspan=2, sticky="w", **pad)

        tk.Label(self.root, text="現在の電流 [A]").grid(row=4, column=0, sticky="e", **pad)
        tk.Label(self.root, textvariable=self.meas_i_var, width=16, anchor="w").grid(row=4, column=1, columnspan=2, sticky="w", **pad)

        tk.Label(self.root, textvariable=self.log_status_var).grid(row=5, column=0, columnspan=3, **pad)

    def is_output_on(self):
        try:
            return self.ctrl.get_output_state()
        except Exception:
            return False

    def update_ui_state(self):
        on = self.is_output_on()

        if on:
            self.btn_onoff.config(text="ON", bg="red")
            self.entry_target.config(state="normal")
            self.btn_set.config(state="normal")
        else:
            self.btn_onoff.config(text="OFF", bg="light gray")
            self.entry_target.config(state="disabled")
            self.btn_set.config(state="disabled")

        if self.logger.running:
            self.btn_log.config(text="ログ停止")
            if self.logger.filename:
                self.log_status_var.set(f"LOG: ON ({self.logger.filename})")
            else:
                self.log_status_var.set("LOG: ON")
        else:
            self.btn_log.config(text="ログ開始")
            self.log_status_var.set("LOG: OFF")

    def update_measurements(self):
        try:
            v = self.ctrl.measure_voltage()
            i = self.ctrl.measure_current()
            self.meas_v_var.set(f"{v:.3f}")
            self.meas_i_var.set(f"{i:.6f}")
        except Exception:
            self.meas_v_var.set("ERR")
            self.meas_i_var.set("ERR")

        self.update_ui_state()
        self.root.after(POLL_INTERVAL_MS, self.update_measurements)

    def on_toggle_output(self):
        try:
            if self.is_output_on():
                self.ctrl.output_off()
            else:
                # ON時は必ず0V
                self.ctrl.output_on()
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self.update_ui_state()

    def on_set_voltage(self):
        if self.ramp_thread is not None and self.ramp_thread.is_alive():
            messagebox.showwarning("Busy", "電圧変更中です。")
            return

        try:
            target = float(self.target_var.get())
        except ValueError:
            messagebox.showerror("Error", "設定電圧が不正です。")
            return

        if target < 0 or target > MAX_VOLTAGE:
            messagebox.showerror("Error", f"設定電圧は 0 ～ {MAX_VOLTAGE} V にしてください。")
            return

        def worker():
            try:
                ramp_voltage(self.ctrl, target, log_func=lambda msg: None)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

        self.ramp_thread = threading.Thread(target=worker, daemon=True)
        self.ramp_thread.start()

    def on_toggle_log(self):
        try:
            if self.logger.running:
                self.logger.stop()
            else:
                self.logger.start()
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self.update_ui_state()


# =========================================================
# CUI
# =========================================================
def run_cui():
    ctrl = PMXController()
    logger = PMXLogger(ctrl)

    print(f"Connecting to {PMX_IP}:{PMX_PORT}")
    ctrl.initialize()

    if ENABLE_LOG:
        logger.start()
        print(f"Logging started: {logger.filename}")

    try:
        ctrl.output_on()
        print("Output ON (0 V fixed)")
        ramp_voltage(ctrl, TARGET_VOLTAGE)
        v = ctrl.measure_voltage()
        i = ctrl.measure_current()
        print(f"Measured: {v:.3f} V, {i:.6f} A")
        print("Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        logger.stop()
        ctrl.close()


# =========================================================
# Main
# =========================================================
def main():
    if USE_GUI:
        if not TK_AVAILABLE:
            print("tkinter is not available. Set USE_GUI = False.")
            return
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    else:
        run_cui()


if __name__ == "__main__":
    main()
