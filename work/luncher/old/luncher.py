import os
import sys
import json
import ctypes
import subprocess
import winreg
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

# Windows API Imports (pywin32)
try:
    import win32api
    import win32con
    import win32process
    import win32security
    import win32profile
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

CONFIG_FILENAME = "luncher_config.json"


def is_admin() -> bool:
    """Checks if the script is running with administrative privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    """Relaunches the current script with Administrator rights via UAC prompt."""
    if is_admin():
        return True

    # Get python execution parameters
    script = os.path.abspath(sys.argv[0])
    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
    
    # If compiled as EXE or running script via python interpreter
    if getattr(sys, 'frozen', False):
        executable = sys.executable
        arguments = params
    else:
        executable = sys.executable
        arguments = f'"{script}" {params}'

    try:
        # ShellExecuteW with 'runas' verb triggers the UAC prompt
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, arguments, None, 1
        )
        # Ret > 32 indicates success
        if ret > 32:
            sys.exit(0)
        else:
            return False
    except Exception:
        return False


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


def launch_as_user_admin(target_user: str, exe_path: str, cwd: str):
    """Launches a program as target_user without password using administrative privileges."""
    if not PYWIN32_AVAILABLE:
        raise RuntimeError("pywin32 library is required for admin impersonation. Install with: pip install pywin32")

    # Acquire necessary privileges to handle process tokens
    flags = win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY
    htoken = win32security.OpenProcessToken(win32process.GetCurrentProcess(), flags)
    
    privs = [
        (win32security.LookupPrivilegeValue(None, win32security.SE_ASSIGNPRIMARYTOKEN_NAME), win32security.SE_PRIVILEGE_ENABLED),
        (win32security.LookupPrivilegeValue(None, win32security.SE_INCREASE_QUOTA_NAME), win32security.SE_PRIVILEGE_ENABLED)
    ]
    win32security.AdjustTokenPrivileges(htoken, 0, privs)

    # Find the target user's active process token or session token
    user_token = None
    
    for pid in win32process.EnumProcesses():
        try:
            hproc = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
            ptoken = win32security.OpenProcessToken(hproc, win32security.TOKEN_DUPLICATE | win32security.TOKEN_QUERY)
            sid, _ = win32security.GetTokenInformation(ptoken, win32security.TokenUser)
            account_name, _, _ = win32security.LookupAccountSid(None, sid)
            
            if account_name.lower() == target_user.lower():
                user_token = win32security.DuplicateTokenEx(
                    ptoken,
                    win32security.MAXIMUM_ALLOWED,
                    None,
                    win32security.SecurityImpersonation,
                    win32security.TokenPrimary
                )
                win32api.CloseHandle(hproc)
                break
            win32api.CloseHandle(hproc)
        except Exception:
            continue

    if user_token:
        environment = win32profile.CreateEnvironmentBlock(user_token, False)
        startup = win32process.STARTUPINFO()
        
        win32process.CreateProcessAsUser(
            user_token,
            exe_path,
            None,
            None,
            None,
            False,
            win32con.CREATE_UNICODE_ENVIRONMENT,
            environment,
            cwd,
            startup
        )
    else:
        raise RuntimeError(f"No active session or process found for user '{target_user}'. User must be logged in.")


class LuncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("luncher (Administrator)")
        self.geometry("620x420")
        self.minsize(500, 300)

        self.selected_user = tk.StringVar(value=os.getlogin())
        self.programs = load_or_create_config()
        self.users = get_local_windows_users()

        self.user_buttons = {}
        self._build_ui()

    def _build_ui(self):
        status_text = f"Config: {get_user_config_path()} | Admin Rights: Active"
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

        # Left Column: Users
        left_frame = ttk.LabelFrame(main_frame, text=" Select User ", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

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
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

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

        # Launching as another user (running elevated)
        try:
            launch_as_user_admin(target_user, exe_path, cwd)
        except Exception as e:
            messagebox.showwarning("Admin Launch Warning", f"Direct token launch failed ({e}). Falling back to RUNAS...")
            cmd = f'runas /user:{target_user} "{exe_path}"'
            subprocess.Popen(f'cmd.exe /c start "Luncher" {cmd}', cwd=cwd)


if __name__ == "__main__":
    # Elevate privilege before launching UI
    if not is_admin():
        if not relaunch_as_admin():
            print("Elevation denied. Exiting.")
            sys.exit(1)
    else:
        app = LuncherApp()
        app.mainloop()