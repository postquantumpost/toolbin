#!/usr/bin/env python3
#
 
import os
import sys
import json
import subprocess
import winreg
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

CONFIG_FILENAME = "luncher_config.json"


def relaunch_under_pythonw() -> bool:
    """Relaunches this script without a console when started with python.exe."""
    if getattr(sys, "frozen", False) or Path(sys.executable).name.lower() == "pythonw.exe":
        return False

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        messagebox.showerror("Launcher Error", f"Could not find pythonw.exe beside:\n{sys.executable}")
        raise SystemExit(1)

    try:
        subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError as e:
        messagebox.showerror("Launcher Error", f"Could not start pythonw.exe:\n{e}")
        raise SystemExit(1) from e
    return True


def get_user_config_path() -> Path:
    """Returns the path to the config file in the current user's home directory."""
    return Path.home() / CONFIG_FILENAME


def detect_installed_browsers() -> list[dict[str, str]]:
    """Detects installed web browsers via Windows Registry."""
    detected = []
    candidates = [
        ("Google Chrome", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ("Mozilla Firefox", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"),
        ("Microsoft Edge", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
        ("Brave", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"),
    ]

    for name, reg_key in candidates:
        exe_path = None
        for hkey in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hkey, reg_key) as key:
                    exe_path, _ = winreg.QueryValueEx(key, "")
                    if exe_path and os.path.exists(exe_path):
                        break
            except OSError:
                continue

        if exe_path and os.path.exists(exe_path):
            detected.append({"name": name, "path": exe_path})

    return detected


def load_or_create_config() -> list[dict[str, str]]:
    """Loads programs from the JSON config or initializes it with detected browsers."""
    config_path = get_user_config_path()
    
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "programs" in data:
                    return data["programs"]
        except Exception as e:
            messagebox.showwarning("Config Warning", f"Could not read config ({e}). Reverting to defaults.")

    default_programs = detect_installed_browsers()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_programs, f, indent=4)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to create config file at {config_path}: {e}")

    return default_programs


def get_local_windows_users() -> list[str]:
    """Retrieves local Windows user accounts using PowerShell/WMIC fallback."""
    users = []
    try:
        cmd = ["powershell", "-Command", "Get-LocalUser | Where-Object {$_.Enabled -eq $True} | Select-Object -ExpandProperty Name"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        users = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    except Exception:
        users_dir = Path("C:/Users")
        if users_dir.exists():
            ignored = {"Public", "Default", "Default User", "All Users"}
            users = [d.name for d in users_dir.iterdir() if d.is_dir() and d.name not in ignored]

    current_user = os.getlogin()
    if current_user not in users:
        users.insert(0, current_user)

    return sorted(list(set(users)))


class LuncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("luncher")
        self.geometry("620x420")
        self.minsize(500, 300)

        self.selected_user = tk.StringVar(value=os.getlogin())
        self.programs = load_or_create_config()
        self.users = get_local_windows_users()

        self.user_buttons = {}
        self._build_ui()

    def _build_ui(self):
        status_text = f"Config: {get_user_config_path()}"
        status_label = ttk.Label(
            self, 
            text=status_text, 
            anchor="w", 
            padding=5,
            relief="sunken"
        )
        status_label.pack(side=tk.BOTTOM, fill=tk.X)

        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1, uniform="panels")
        main_frame.grid_columnconfigure(1, weight=1, uniform="panels")
        main_frame.grid_rowconfigure(0, weight=1)

        # Left Column: Users
        left_frame = ttk.LabelFrame(main_frame, text=" Select User ", padding=10)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        user_canvas = tk.Canvas(left_frame, highlightthickness=0)
        user_scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=user_canvas.yview)
        user_scroll_frame = ttk.Frame(user_canvas)

        user_scroll_frame.bind(
            "<Configure>",
            lambda e: user_canvas.configure(scrollregion=user_canvas.bbox("all"))
        )
        user_canvas.create_window((0, 0), window=user_scroll_frame, anchor="nw")
        user_canvas.configure(yscrollcommand=user_scrollbar.set)

        user_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        user_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        for user in self.users:
            btn = tk.Button(
                user_scroll_frame,
                text=user,
                font=("Segoe UI", 10, "bold"),
                anchor="w",
                padx=10,
                pady=6,
                relief="flat",
                command=lambda u=user: self.select_user(u)
            )
            btn.pack(fill=tk.X, expand=True, pady=2)
            self.user_buttons[user] = btn

        # Right Column: Programs
        right_frame = ttk.LabelFrame(main_frame, text=" Launch Program ", padding=10)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        prog_canvas = tk.Canvas(right_frame, highlightthickness=0)
        prog_scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=prog_canvas.yview)
        prog_scroll_frame = ttk.Frame(prog_canvas)

        prog_scroll_frame.bind(
            "<Configure>",
            lambda e: prog_canvas.configure(scrollregion=prog_canvas.bbox("all"))
        )
        prog_canvas.create_window((0, 0), window=prog_scroll_frame, anchor="nw")
        prog_canvas.configure(yscrollcommand=prog_scrollbar.set)

        prog_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        prog_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        if not self.programs:
            lbl = ttk.Label(prog_scroll_frame, text="No programs configured in JSON.")
            lbl.pack(pady=10)
        else:
            for prog in self.programs:
                name = prog.get("name", "Unknown Program")
                path = prog.get("path", "")
                btn = ttk.Button(
                    prog_scroll_frame,
                    text=f"🚀 Launch {name}",
                    command=lambda p=path: self.launch_program(p)
                )
                btn.pack(fill=tk.X, expand=True, pady=4, ipady=4)

        self.update_user_highlights()

    def select_user(self, user: str):
        self.selected_user.set(user)
        self.update_user_highlights()

    def update_user_highlights(self):
        selected = self.selected_user.get()
        for user, btn in self.user_buttons.items():
            if user == selected:
                btn.config(bg="#0078D7", fg="white", activebackground="#005A9E", activeforeground="white")
            else:
                btn.config(bg="#E1E1E1", fg="black", activebackground="#D0D0D0", activeforeground="black")

    def launch_program(self, exe_path: str):
        target_user = self.selected_user.get()
        user_home = Path(f"C:/Users/{target_user}")
        cwd = str(user_home if user_home.exists() else Path.home())

        if not os.path.exists(exe_path):
            messagebox.showerror("Error", f"Program path does not exist:\n{exe_path}")
            return

        current_user = os.getlogin()

        # Launching as current user
        if target_user.lower() == current_user.lower():
            subprocess.Popen([exe_path], cwd=cwd)
            return

        try:
            cmd = f'runas /savecred /user:{target_user} "{exe_path}"'
            subprocess.Popen(f'cmd.exe /c start "Luncher" {cmd}', cwd=cwd)
        except Exception as e:
            messagebox.showerror("Launch Failed", f"Failed to launch program:\n{e}")


if __name__ == "__main__":
    if relaunch_under_pythonw():
        sys.exit(0)

    app = LuncherApp()
    app.mainloop()