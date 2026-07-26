from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import ctypes
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "OpsNest"
SETUP_NAME = "OpsNest Setup"
APP_VERSION = "2.13.3"
UNINSTALL_KEY = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"


def base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def resource_path(*parts: str) -> Path:
    return base_dir().joinpath(*parts)


def find_payload_dir() -> Path:
    candidates = [
        resource_path("payload", APP_NAME),
        resource_path(APP_NAME),
        Path(__file__).resolve().parent / "release" / APP_NAME,
        Path.cwd() / "release" / APP_NAME,
    ]
    for candidate in candidates:
        if (candidate / f"{APP_NAME}.exe").exists():
            return candidate
    raise FileNotFoundError("OpsNest folder nije pronađen. Pokreni build aplikacije pre instalera.")


def find_payload_app() -> Path:
    return find_payload_dir() / f"{APP_NAME}.exe"


def installer_executable() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def desktop_shortcut_path() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{APP_NAME}.lnk"


def start_menu_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME


def default_install_dir() -> Path:
    """Use a per-user program folder so installation does not require admin rights."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return local_app_data / "Programs" / APP_NAME


def setup_log_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return local_app_data / APP_NAME / "Logs" / "setup.log"


def update_backup_dir(install_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return install_dir.parent / f".{APP_NAME}-backup-{stamp}"


def append_setup_log(message: str) -> None:
    try:
        path = setup_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message.rstrip()}\n")
    except OSError:
        # An installer should still work when Windows blocks an optional log file.
        pass


def opsnest_is_running() -> bool:
    """Avoid deleting files that are locked by an already running OpsNest."""
    if not sys.platform.startswith("win"):
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {APP_NAME}.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return f"{APP_NAME}.exe".lower() in result.stdout.lower()
    except OSError:
        return False


def register_control_panel_entry(install_dir: Path, app_exe: Path, uninstaller_exe: Path) -> None:
    """Make the app visible in Windows Installed apps and Programs and Features."""
    if not sys.platform.startswith("win"):
        return
    import winreg

    estimated_size_kb = max(1, (app_exe.stat().st_size + uninstaller_exe.stat().st_size) // 1024)
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": APP_VERSION,
        "Publisher": "OpsNest",
        "DisplayIcon": f"{app_exe},0",
        "InstallLocation": str(install_dir),
        "UninstallString": f'"{uninstaller_exe}" --uninstall',
        "QuietUninstallString": f'"{uninstaller_exe}" --uninstall',
        "InstallDate": date.today().strftime("%Y%m%d"),
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": estimated_size_kb,
    }
    roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    last_error: OSError | None = None
    for root in roots:
        try:
            with winreg.CreateKeyEx(root, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
                for name, value in values.items():
                    value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                    winreg.SetValueEx(key, name, 0, value_type, value)
            return
        except OSError as exc:
            last_error = exc
    if last_error:
        raise last_error


def unregister_control_panel_entry() -> None:
    if not sys.platform.startswith("win"):
        return
    import winreg

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            winreg.DeleteKey(root, UNINSTALL_KEY)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def schedule_install_folder_cleanup(install_dir: Path) -> None:
    cleanup = install_dir / "_remove_opsnest.cmd"
    cleanup.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d C:\\",
                "timeout /t 3 /nobreak >nul",
                f'rmdir /s /q "{install_dir}"',
            ]
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(cleanup)],
        cwd=str(Path(os.environ.get("TEMP", r"C:\\Windows\\Temp"))),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def center_window(win: tk.Tk | tk.Toplevel, width: int, height: int) -> None:
    win.update_idletasks()
    x = max(0, (win.winfo_screenwidth() - width) // 2)
    y = max(0, (win.winfo_screenheight() - height) // 3)
    win.geometry(f"{width}x{height}+{x}+{y}")


def enable_high_dpi() -> None:
    """Keep the installer sharp without using an incorrect virtual screen size."""
    if not sys.platform.startswith("win"):
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def create_shortcut(shortcut_path: Path, target_path: Path, *, working_dir: Path | None = None, arguments: str = "", icon_location: Path | None = None) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    working_dir = working_dir or target_path.parent
    ps_script = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$shortcut = $ws.CreateShortcut({json.dumps(str(shortcut_path))})",
        f"$shortcut.TargetPath = {json.dumps(str(target_path))}",
        f"$shortcut.WorkingDirectory = {json.dumps(str(working_dir))}",
    ]
    if arguments:
        ps_script.append(f"$shortcut.Arguments = {json.dumps(arguments)}")
    if icon_location:
        ps_script.append(f"$shortcut.IconLocation = {json.dumps(str(icon_location) + ',0')}")
    ps_script.append("$shortcut.Save()")
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", "\n".join(ps_script)],
        check=True,
    )


def replace_program_files(payload_dir: Path, install_dir: Path) -> Path:
    """Stage a replacement first so a failed update can restore the old program."""
    if not install_dir.is_absolute() or len(install_dir.parts) <= 1:
        raise ValueError("Izaberite pun folder za instalaciju.")
    parent = install_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage_dir = parent / f".{APP_NAME}-update-stage"
    backup_dir = update_backup_dir(install_dir)
    shutil.rmtree(stage_dir, ignore_errors=True)
    shutil.copytree(payload_dir, stage_dir)
    try:
        if install_dir.exists():
            install_dir.rename(backup_dir)
        stage_dir.rename(install_dir)
    except Exception:
        if not install_dir.exists() and backup_dir.exists():
            backup_dir.rename(install_dir)
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)
    return install_dir / f"{APP_NAME}.exe"


class InstallerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(SETUP_NAME)
        icon = resource_path("assets", "opsnest.ico")
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        self.resizable(False, False)
        self.configure(background="#F4FBF7")
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
        except tk.TclError:
            style = ttk.Style(self)
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#F4FBF7", foreground="#245B41", font=("Segoe UI", 16, "bold"))
        style.configure("Body.TLabel", background="#F4FBF7", foreground="#22332C")
        style.configure("TLabelFrame", background="#F4FBF7", foreground="#245B41")
        style.configure("TCheckbutton", background="#F4FBF7", foreground="#22332C")

        self.payload_dir = find_payload_dir()
        self.payload_app = self.payload_dir / f"{APP_NAME}.exe"
        self.install_dir_var = tk.StringVar(value=str(default_install_dir()))
        self.desktop_shortcut_var = tk.BooleanVar(value=True)
        self.start_menu_shortcut_var = tk.BooleanVar(value=True)
        self.launch_after_install_var = tk.BooleanVar(value=True)

        outer = ttk.Frame(self, padding=16, style="TFrame")
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=SETUP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Instalira lokalnu poslovnu aplikaciju za fakture, kupce, projekte, e-mail slanje i PDF/Excel izlaze.",
            style="Body.TLabel",
            wraplength=560,
        ).pack(anchor="w", pady=(4, 12))

        install_box = ttk.LabelFrame(outer, text="Instalaciona lokacija", padding=10)
        install_box.pack(fill="x")
        install_box.columnconfigure(0, weight=1)
        self.install_entry = ttk.Entry(install_box, textvariable=self.install_dir_var, width=56)
        self.install_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(install_box, text="Izaberi", command=self.browse_install_dir).grid(row=0, column=1, sticky="e")

        options = ttk.LabelFrame(outer, text="Opcije", padding=10)
        options.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(options, text="Desktop shortcut", variable=self.desktop_shortcut_var).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="Start meni shortcut", variable=self.start_menu_shortcut_var).grid(row=0, column=1, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="Pokreni aplikaciju posle instalacije", variable=self.launch_after_install_var).grid(row=0, column=2, sticky="w")

        log_box = ttk.LabelFrame(outer, text="Status", padding=10)
        log_box.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_box, height=7, wrap="word", background="white", foreground="#1F2937", relief="flat", borderwidth=0)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.insert("1.0", f"OpsNest aplikacija:\n{self.payload_app}\n\n")
        self.log_text.configure(state="disabled")
        append_setup_log(f"{SETUP_NAME} {APP_VERSION} opened.")

        ttk.Separator(outer).pack(fill="x", pady=(14, 10))
        buttons = tk.Frame(outer, background="#F4FBF7")
        buttons.pack(fill="x")
        self.install_button = tk.Button(
            buttons,
            text="Instaliraj / Ažuriraj OpsNest",
            command=self.install,
            background="#087E72",
            foreground="white",
            activebackground="#06675F",
            activeforeground="white",
            disabledforeground="#E2E8F0",
            font=("Segoe UI", 11, "bold"),
            padx=20,
            pady=10,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
        )
        self.install_button.pack(side="left")
        self.cancel_button = tk.Button(
            buttons,
            text="Otkaži",
            command=self.destroy,
            background="#FFFFFF",
            foreground="#334155",
            activebackground="#E7EEF5",
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=10,
            relief="solid",
            borderwidth=1,
            cursor="hand2",
        )
        self.cancel_button.pack(side="right")

        center_window(self, 720, 570)
        self.install_entry.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.install())

    def log(self, message: str) -> None:
        append_setup_log(message)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.update_idletasks()

    def browse_install_dir(self) -> None:
        folder = filedialog.askdirectory(title="Izaberi instalacioni folder", initialdir=self.install_dir_var.get())
        if folder:
            self.install_dir_var.set(folder)

    def _install_uninstaller(self, install_dir: Path) -> Path:
        uninstaller_exe = install_dir / f"Uninstall {APP_NAME}.exe"
        shutil.copy2(installer_executable(), uninstaller_exe)
        return uninstaller_exe

    def _set_installing(self, is_installing: bool) -> None:
        if is_installing:
            self.install_button.configure(state="disabled", text="Instalacija je u toku...")
            self.cancel_button.configure(state="disabled")
        else:
            self.install_button.configure(state="normal", text="Instaliraj / Ažuriraj OpsNest")
            self.cancel_button.configure(state="normal")
        self.update_idletasks()

    def install(self) -> None:
        try:
            if opsnest_is_running():
                messagebox.showwarning(
                    SETUP_NAME,
                    "OpsNest je trenutno otvoren. Zatvori aplikaciju, pa ponovo klikni Instaliraj.",
                )
                return
            install_dir = Path(self.install_dir_var.get()).expanduser()
            if not install_dir.is_absolute():
                install_dir = install_dir.resolve()
            if len(install_dir.parts) <= 1:
                messagebox.showerror(SETUP_NAME, "Izaberi pun folder za instalaciju.")
                return
            if install_dir.exists() and not install_dir.is_dir():
                messagebox.showerror(SETUP_NAME, "Izabrana putanja nije folder.")
                return
            if install_dir.exists() and install_dir.is_dir() and any(install_dir.iterdir()):
                if not messagebox.askyesno(SETUP_NAME, f"Folder već postoji:\n{install_dir}\n\nDa li da ga zameniš?"):
                    return

            self._set_installing(True)
            self.log(f"Instalacija u: {install_dir}")
            # Stage the new app beside the old folder. User data lives outside
            # this folder, so updating never touches the accounting database.
            try:
                app_exe = replace_program_files(self.payload_dir, install_dir)
            except PermissionError as exc:
                raise PermissionError(
                    "Windows ne može da zameni staru verziju. Zatvori OpsNest i File Explorer prozor "
                    "koji je otvoren u instalacionom folderu, pa pokušaj ponovo."
                ) from exc
            self.log("Brzi OpsNest paket je kopiran.")

            uninstaller_exe = self._install_uninstaller(install_dir)
            register_control_panel_entry(install_dir, app_exe, uninstaller_exe)
            self.log("Control Panel uninstaller je registrovan.")

            if self.desktop_shortcut_var.get():
                shortcut = desktop_shortcut_path()
                shortcut.parent.mkdir(parents=True, exist_ok=True)
                create_shortcut(shortcut, app_exe, working_dir=install_dir, icon_location=app_exe)
                self.log("Desktop shortcut je napravljen.")

            if self.start_menu_shortcut_var.get():
                menu_dir = start_menu_dir()
                create_shortcut(menu_dir / f"{APP_NAME}.lnk", app_exe, working_dir=install_dir, icon_location=app_exe)
                create_shortcut(menu_dir / f"Uninstall {APP_NAME}.lnk", uninstaller_exe, working_dir=install_dir, arguments="--uninstall", icon_location=uninstaller_exe)
                self.log("Start meni shortcut je napravljen.")

            messagebox.showinfo(SETUP_NAME, f"OpsNest je instaliran u:\n{install_dir}")
            if self.launch_after_install_var.get():
                try:
                    os.startfile(str(app_exe))  # noqa: S606
                except OSError:
                    pass
            self.destroy()
        except Exception as exc:
            self._set_installing(False)
            append_setup_log(f"INSTALL ERROR: {type(exc).__name__}: {exc}")
            messagebox.showerror(
                SETUP_NAME,
                f"Instalacija nije uspela:\n{exc}\n\nLog: {setup_log_path()}",
            )


class AutoUpdateApp(tk.Tk):
    """Small handoff window used after the desktop app has verified an update."""

    def __init__(self, install_dir: Path, restart: bool) -> None:
        super().__init__()
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#0F5B50")
        style.configure("Body.TLabel", font=("Segoe UI", 10), foreground="#294B62")
        self.install_dir = install_dir
        self.restart = restart
        self.payload_dir = find_payload_dir()
        self.title(f"{APP_NAME} ažuriranje")
        self.configure(background="#F4FBF7")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="OpsNest se ažurira", style="Title.TLabel").pack(anchor="w")
        self.status_var = tk.StringVar(value="Zatvaramo staru verziju i pripremamo bezbedno ažuriranje...")
        ttk.Label(outer, textvariable=self.status_var, style="Body.TLabel", wraplength=500).pack(anchor="w", pady=(8, 14))
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x")
        self.progress.start(12)
        center_window(self, 600, 220)
        self.after(120, self.apply_update)

    def apply_update(self) -> None:
        try:
            deadline = time.monotonic() + 90
            while opsnest_is_running() and time.monotonic() < deadline:
                self.update_idletasks()
                time.sleep(0.35)
            if opsnest_is_running():
                raise RuntimeError("OpsNest je i dalje otvoren. Zatvorite ga pa pokušajte ažuriranje ponovo.")
            self.status_var.set("Zamenjujemo samo programske fajlove. Lokalni podaci ostaju sačuvani...")
            self.update_idletasks()
            app_exe = replace_program_files(self.payload_dir, self.install_dir)
            uninstaller_exe = self.install_dir / f"Uninstall {APP_NAME}.exe"
            shutil.copy2(installer_executable(), uninstaller_exe)
            register_control_panel_entry(self.install_dir, app_exe, uninstaller_exe)
            append_setup_log(f"Auto-update completed: {APP_VERSION} -> {self.install_dir}")
            self.status_var.set("Ažuriranje je završeno. OpsNest se ponovo pokreće...")
            self.update_idletasks()
            if self.restart:
                subprocess.Popen([str(app_exe)], cwd=str(self.install_dir))
            self.after(450, self.destroy)
        except Exception as exc:
            append_setup_log(f"AUTO UPDATE ERROR: {type(exc).__name__}: {exc}")
            self.progress.stop()
            messagebox.showerror(
                f"{APP_NAME} ažuriranje",
                f"Ažuriranje nije uspelo:\n{exc}\n\nStara verzija nije obrisana. Log: {setup_log_path()}",
                parent=self,
            )
            self.destroy()


class UninstallerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Uninstall {APP_NAME}")
        icon = installer_executable()
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        self.resizable(False, False)
        self.configure(background="#F4FBF7")
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
        except tk.TclError:
            style = ttk.Style(self)
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#F4FBF7", foreground="#245B41", font=("Segoe UI", 16, "bold"))
        style.configure("Body.TLabel", background="#F4FBF7", foreground="#22332C")
        style.configure("Danger.TButton", background="#B42318", foreground="white", padding=(12, 7))
        style.map("Danger.TButton", background=[("active", "#8F1D14")])

        self.install_dir = installer_executable().parent
        self.remove_data_var = tk.BooleanVar(value=False)
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"Deinstaliraj {APP_NAME}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Aplikacija, desktop prečica, Start meni prečica i Control Panel unos biće uklonjeni. "
                "Podaci, baze i fakture ostaju sačuvani osim ako izričito ne izaberete brisanje."
            ),
            style="Body.TLabel",
            wraplength=560,
        ).pack(anchor="w", pady=(6, 12))
        ttk.Label(outer, text=f"Lokacija programa: {self.install_dir}", style="Body.TLabel", wraplength=560).pack(anchor="w")
        ttk.Checkbutton(
            outer,
            text="Obriši i lokalne podatke iz C:\\OpsNest (ne može da se vrati)",
            variable=self.remove_data_var,
        ).pack(anchor="w", pady=(12, 0))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text="Deinstaliraj", style="Danger.TButton", command=self.uninstall).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 660, 320)
        self.bind("<Escape>", lambda e: self.destroy())

    def uninstall(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Da li želite da deinstalirate OpsNest?"):
            return
        try:
            desktop_shortcut_path().unlink(missing_ok=True)
            shutil.rmtree(start_menu_dir(), ignore_errors=True)
            unregister_control_panel_entry()
            if self.remove_data_var.get():
                root_dir = Path(os.environ.get("DELTA_FAKTURE_ROOT", r"C:\OpsNest"))
                if root_dir.exists():
                    shutil.rmtree(root_dir)
            schedule_install_folder_cleanup(self.install_dir)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Deinstalacija nije uspela:\n{exc}")
            return
        data_message = "i lokalni podaci" if self.remove_data_var.get() else "dok su lokalni podaci sačuvani"
        messagebox.showinfo(APP_NAME, f"OpsNest je uklonjen, {data_message}.")
        self.destroy()


def main() -> int:
    enable_high_dpi()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--auto-update", action="store_true")
    parser.add_argument("--install-dir")
    parser.add_argument("--restart", action="store_true")
    args, _unknown = parser.parse_known_args()
    if args.auto_update:
        if not args.install_dir:
            raise SystemExit("Missing --install-dir for OpsNest auto-update.")
        app = AutoUpdateApp(Path(args.install_dir).expanduser().resolve(), args.restart)
    elif args.uninstall:
        app = UninstallerApp()
    else:
        app = InstallerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
