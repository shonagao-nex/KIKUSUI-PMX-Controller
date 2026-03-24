#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import threading
import time
from datetime import datetime
from typing import Optional

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
ENABLE_LOG = False      # CUI auto logging
TARGET_VOLTAGE = 10.0   # default target voltage [V]

MAX_VOLTAGE = 62.0      # [V]
MAX_CURRENT = 0.010     # [A] = 10 mA

RAMP_STEP_V = 1.0       # [V]
RAMP_INTERVAL_S = 0.1   # [s]

LOG_INTERVAL_S = 10.0   # [s]
POLL_INTERVAL_MS = 1000 # GUI measurement refresh

LOG_EXTENSION = ".csv"


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
        self.write("VOLT:EXT:SOUR NONE")
        self.write("CURR:EXT:SOUR NONE")
        self.write(f"CURR {MAX_CURRENT:.6f}")
        self.write("OUTP:PROT:CLE")
        self.write("VOLT 0.000000")

    def output_on(self):
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

    def emergency_stop(self, stop_event: Optional[threading.Event] = None):
        safe_ramp_to_zero(self, stop_event=stop_event)
        self.write("OUTP 0")


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
def wait_with_stop(total_s: float, stop_event: Optional[threading.Event] = None):
    step = 0.01
    n = max(1, int(total_s / step))
    for _ in range(n):
        if stop_event is not None and stop_event.is_set():
            return False
        time.sleep(step)
    remain = total_s - n * step
    if remain > 0:
        if stop_event is not None and stop_event.is_set():
            return False
        time.sleep(remain)
    return True


def ramp_voltage(ctrl: PMXController, target_v: float, stop_event: Optional[threading.Event] = None):
    if target_v < 0:
        target_v = 0
    if target_v > MAX_VOLTAGE:
        target_v = MAX_VOLTAGE

    current_set = ctrl.get_set_voltage()

    if abs(target_v - current_set) < 1e-9:
        return

    step = RAMP_STEP_V if target_v > current_set else -RAMP_STEP_V
    v = current_set

    while True:
        if stop_event is not None and stop_event.is_set():
            return

        next_v = v + step

        if (step > 0 and next_v >= target_v) or (step < 0 and next_v <= target_v):
            ctrl.set_voltage(target_v)
            break
        else:
            ctrl.set_voltage(next_v)
            v = next_v

        if not wait_with_stop(RAMP_INTERVAL_S, stop_event):
            return


def safe_ramp_to_zero(ctrl: PMXController, stop_event: Optional[threading.Event] = None):
    current_set = ctrl.get_set_voltage()

    if current_set <= 0:
        ctrl.set_voltage(0.0)
        return

    v = current_set
    while v > 0:
        next_v = max(0.0, v - RAMP_STEP_V)
        ctrl.set_voltage(next_v)
        v = next_v
        wait_with_stop(RAMP_INTERVAL_S, stop_event=None)


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
        self.ramp_stop_event = threading.Event()
        self.emergency_thread = None

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

        tk.Label(self.root, text=f"IP: {PMX_IP}", font=("Arial", 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad
        )

        tk.Label(self.root, text="Voltage").grid(row=1, column=0, sticky="e", **pad)
        tk.Label(self.root, textvariable=self.meas_v_var, width=12, anchor="w").grid(row=1, column=1, sticky="w", **pad)

        tk.Label(self.root, text="Current").grid(row=1, column=2, sticky="e", **pad)
        tk.Label(self.root, textvariable=self.meas_i_var, width=12, anchor="w").grid(row=1, column=3, sticky="w", **pad)

        tk.Label(self.root, text="Target Voltage").grid(row=2, column=0, sticky="e", **pad)
        self.entry_target = tk.Entry(self.root, textvariable=self.target_var, width=12)
        self.entry_target.grid(row=2, column=1, sticky="w", **pad)

        self.btn_set = tk.Button(self.root, text="Set Voltage", width=14, command=self.on_set_voltage)
        self.btn_set.grid(row=2, column=2, columnspan=2, sticky="ew", **pad)

        self.btn_emergency = tk.Button(
            self.root, text="Emergency Stop", width=14, bg="orange red", fg="white", command=self.on_emergency_stop
        )
        self.btn_emergency.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        self.btn_log = tk.Button(self.root, text="Start Log", width=14, command=self.on_toggle_log)
        self.btn_log.grid(row=3, column=2, columnspan=2, sticky="ew", **pad)

        self.btn_onoff = tk.Button(self.root, text="OFF", width=14, bg="light gray", command=self.on_toggle_output)
        self.btn_onoff.grid(row=4, column=0, columnspan=4, sticky="ew", **pad)

        self.btn_onoff.bind("<Enter>", self.on_onoff_hover_enter)
        self.btn_onoff.bind("<Leave>", self.on_onoff_hover_leave)

        tk.Label(self.root, textvariable=self.log_status_var).grid(
            row=5, column=0, columnspan=4, sticky="w", **pad
        )

    def is_output_on(self):
        try:
            return self.ctrl.get_output_state()
        except Exception:
            return False

    def update_onoff_button_normal(self):
        on = self.is_output_on()
        if on:
            self.btn_onoff.config(text="ON", bg="red", activebackground="red", fg="white")
        else:
            self.btn_onoff.config(text="OFF", bg="light gray", activebackground="light gray", fg="black")

    def on_onoff_hover_enter(self, event=None):
        on = self.is_output_on()
        if on:
            self.btn_onoff.config(text="OFF", bg="red", activebackground="red", fg="white")
        else:
            self.btn_onoff.config(text="ON", bg="light gray", activebackground="light gray", fg="black")

    def on_onoff_hover_leave(self, event=None):
        self.update_onoff_button_normal()

    def update_ui_state(self):
        busy = (
            (self.ramp_thread is not None and self.ramp_thread.is_alive()) or
            (self.emergency_thread is not None and self.emergency_thread.is_alive())
        )
        on = self.is_output_on()

        self.update_onoff_button_normal()

        if on and not busy:
            self.entry_target.config(state="normal")
            self.btn_set.config(state="normal")
        else:
            self.entry_target.config(state="disabled")
            self.btn_set.config(state="disabled")

        if self.logger.running:
            self.btn_log.config(text="Stop Log")
            if self.logger.filename:
                self.log_status_var.set(f"LOG: ON ({self.logger.filename})")
            else:
                self.log_status_var.set("LOG: ON")
        else:
            self.btn_log.config(text="Start Log")
            self.log_status_var.set("LOG: OFF")

    def update_measurements(self):
        try:
            v = self.ctrl.measure_voltage()
            i = self.ctrl.measure_current()
            self.meas_v_var.set(f"{v:.3f} V")
            self.meas_i_var.set(f"{i:.6f} A")
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
                self.ctrl.output_on()
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self.update_ui_state()

    def on_set_voltage(self):
        if self.ramp_thread is not None and self.ramp_thread.is_alive():
            messagebox.showwarning("Busy", "Voltage ramp is in progress.")
            return
        if self.emergency_thread is not None and self.emergency_thread.is_alive():
            messagebox.showwarning("Busy", "Emergency stop is in progress.")
            return

        try:
            target = float(self.target_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid target voltage.")
            return

        if target < 0 or target > MAX_VOLTAGE:
            messagebox.showerror("Error", f"Target voltage must be between 0 and {MAX_VOLTAGE} V.")
            return

        self.ramp_stop_event.clear()

        def worker():
            try:
                ramp_voltage(self.ctrl, target, stop_event=self.ramp_stop_event)
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

    def on_emergency_stop(self):
        if self.emergency_thread is not None and self.emergency_thread.is_alive():
            return

        self.ramp_stop_event.set()

        def worker():
            try:
                if self.ramp_thread is not None and self.ramp_thread.is_alive():
                    self.ramp_thread.join(timeout=1.0)
                self.ctrl.emergency_stop()
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

        self.emergency_thread = threading.Thread(target=worker, daemon=True)
        self.emergency_thread.start()
        self.update_ui_state()


# =========================================================
# CUI
# =========================================================
def run_cui():
    ctrl = PMXController()
    logger = PMXLogger(ctrl)
    ramp_stop_event = threading.Event()

    print(f"IP: {PMX_IP}")
    ctrl.initialize()

    if ENABLE_LOG:
        logger.start()
        print(f"Logging started: {logger.filename}")

    try:
        ctrl.output_on()
        print("Output ON (fixed at 0 V)")
        ramp_voltage(ctrl, TARGET_VOLTAGE, stop_event=ramp_stop_event)
        v = ctrl.measure_voltage()
        i = ctrl.measure_current()
        print(f"Measured: {v:.3f} V, {i:.6f} A")
        print("Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Safe ramp-down to 0 V, then output OFF.")
        ramp_stop_event.set()
        ctrl.emergency_stop()
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
        App(root)
        root.mainloop()
    else:
        run_cui()


if __name__ == "__main__":
    main()
