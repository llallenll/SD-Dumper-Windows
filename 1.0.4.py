import smbclient
import smbclient.shutil
import os
import hashlib
import sqlite3
import time
import os
import sys
import json
if os.name == 'nt':
    import msvcrt
    import tempfile
    lockfile = os.path.join(tempfile.gettempdir(), 'sd_uploader.lock')
    try:
        if os.path.exists(lockfile):
            lock = open(lockfile, 'r+')
        else:
            lock = open(lockfile, 'w')
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        from tkinter import messagebox, Tk
        root = Tk()
        root.withdraw()
        messagebox.showwarning("Already Running", "SD Uploader is already running.")
        sys.exit()

from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, StringVar, BooleanVar, Checkbutton
import pystray
from PIL import Image, ImageDraw
import sys
import signal

# Load settings from settings.json
SETTINGS_FILE = 'settings.json'
DEFAULT_SETTINGS = {
    "SD_LABEL": "F:\\",
    "SMB_SERVER": "192.168.1.254",
    "SMB_SHARE": "Media/Path",
    "SMB_USER": "user",
    "SMB_PASS": "pass",
    "ALLOWED_EXTENSIONS": {".ARW": True, ".JPEG": True, ".MP4": False}
}
def load_settings():
    global SD_LABEL, SMB_SERVER, SMB_SHARE, SMB_USER, SMB_PASS, ALLOWED_EXTENSIONS
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = DEFAULT_SETTINGS
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
    SD_LABEL = data.get('SD_LABEL', DEFAULT_SETTINGS['SD_LABEL'])
    SMB_SERVER = data.get('SMB_SERVER', DEFAULT_SETTINGS['SMB_SERVER'])
    SMB_SHARE = data.get('SMB_SHARE', DEFAULT_SETTINGS['SMB_SHARE'])
    SMB_USER = data.get('SMB_USER', DEFAULT_SETTINGS['SMB_USER'])
    SMB_PASS = data.get('SMB_PASS', DEFAULT_SETTINGS['SMB_PASS'])
    ALLOWED_EXTENSIONS = data.get('ALLOWED_EXTENSIONS', DEFAULT_SETTINGS['ALLOWED_EXTENSIONS'])
APP_VERSION = "1.0.4"

# --- CONFIG ---
SD_LABEL = "F:\\"
SMB_SERVER = "192.168.1.254"
SMB_SHARE = "Media/Path"
SMB_USER = "user"
SMB_PASS = "pass"
DB_PATH = "uploaded_files.db"
ALLOWED_EXTENSIONS = {".ARW": True, ".JPEG": True, ".MP4": False}

# --- SMB Setup ---
def register_smb():
    smbclient.register_session(server=SMB_SERVER, username=SMB_USER, password=SMB_PASS)

# --- STORAGE CALC ---
def get_local_free_space(path):
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize

def get_file_size(path):
    return os.path.getsize(path)

def get_total_upload_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for file in files:
            ext = Path(file).suffix.upper()
            if not ALLOWED_EXTENSIONS.get(ext, False):
                continue
            file_path = str(Path(root) / file)
            if os.path.exists(file_path):
                total += get_file_size(file_path)
    return total

def estimate_smb_free_space(remote_path):
    try:
        stat = smbclient.statvfs(remote_path)
        return stat.f_bavail * stat.f_frsize
    except Exception:
        return float('inf')  # Assume enough if can't determine

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS uploaded_files (file_hash TEXT PRIMARY KEY)")
    cursor.execute("CREATE TABLE IF NOT EXISTS in_progress_uploads (file_hash TEXT PRIMARY KEY, smb_path TEXT)")
    conn.commit()
    conn.close()

def clear_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM uploaded_files")
    cursor.execute("DELETE FROM in_progress_uploads")
    conn.commit()
    conn.close()

def file_hash(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, FileNotFoundError) as e:
        print(f"❌ Cannot read file: {path} - {e}")
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def already_uploaded(conn, hash_val):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM uploaded_files WHERE file_hash=?", (hash_val,))
    return cursor.fetchone() is not None

def mark_uploaded(conn, hash_val):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM in_progress_uploads WHERE file_hash=?", (hash_val,))
    cursor.execute("INSERT INTO uploaded_files (file_hash) VALUES (?)", (hash_val,))
    conn.commit()

def cleanup_incomplete_uploads():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT smb_path FROM in_progress_uploads")
    for row in cursor.fetchall():
        try:
            smbclient.remove(row[0])
        except Exception:
            pass
    cursor.execute("DELETE FROM in_progress_uploads")
    conn.commit()
    conn.close()

# --- FILE UPLOADER ---
def upload_file(local_path, remote_folder, file, log_func, counters):
    conn = sqlite3.connect(DB_PATH)
    try:
        hash_val = file_hash(local_path)
        smb_path = os.path.join(remote_folder, file).replace("\\", "/")
        cursor = conn.cursor()

        if already_uploaded(conn, hash_val):
            log_func(f"⏭️ Skipping (already uploaded): {file}")
            counters['skipped'] += 1
            return

        cursor.execute("INSERT OR REPLACE INTO in_progress_uploads (file_hash, smb_path) VALUES (?, ?)", (hash_val, smb_path))
        conn.commit()

        smbclient.makedirs(os.path.dirname(smb_path), exist_ok=True)
        log_func(f"📤 Uploading {local_path} to {smb_path}")
        smbclient.shutil.copyfile(local_path, smb_path)

        mark_uploaded(conn, hash_val)
        counters['uploaded'] += 1
        counters['remaining'] -= 1
        log_func(f"✅ Uploaded: {file}")
    except Exception as e:
        log_func(f"❌ Error uploading {file}: {e}")
    finally:
        conn.close()

def upload_files(sd_mount, log_func, counters, update_status_func, update_storage_func):
    init_db()
    register_smb()
    cleanup_incomplete_uploads()
    date_folder = datetime.now().strftime("%Y-%m-%d")
    remote_folder = f"//{SMB_SERVER}/{SMB_SHARE}/{date_folder}"

    conn = sqlite3.connect(DB_PATH)
    try:
        for root, _, files in os.walk(sd_mount):
            for file in files:
                ext = Path(file).suffix.upper()
                if not ALLOWED_EXTENSIONS.get(ext, False):
                    continue
                local_path = str((Path(root) / file).resolve())
                if not os.path.exists(local_path):
                    continue
                hash_val = file_hash(local_path)
                if already_uploaded(conn, hash_val):
                    continue
                counters['detected'] += 1
                counters['remaining'] += 1
    finally:
        conn.close()

    update_status_func()
    update_storage_func()

    total_upload_size = get_total_upload_size(sd_mount)
    smb_free = estimate_smb_free_space(remote_folder)

    if smb_free < total_upload_size:
        log_func("❌ Not enough space on SMB share to upload files.")
        return

    with ThreadPoolExecutor(max_workers=4) as executor:
        for root, _, files in os.walk(sd_mount):
            for file in files:
                ext = Path(file).suffix.upper()
                if not ALLOWED_EXTENSIONS.get(ext, False):
                    continue
                local_path = str((Path(root) / file).resolve())
                if not os.path.exists(local_path):
                    continue
                executor.submit(upload_file, local_path, remote_folder, file, log_func, counters)

def check_for_updates(auto=False):
    import urllib.request, shutil, os, sys, subprocess, tempfile
    try:
        with urllib.request.urlopen("https://tlogi.xyz/sd_uploader_version.txt", timeout=5) as response:
            latest_version = response.read().decode().strip()
        if latest_version != APP_VERSION:
            result = messagebox.askyesno("Update Available", f"Version {latest_version} is available. Download and apply now?")
            if result:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
                new_path = exe_path + ".new"
                pid = os.getpid()

                with urllib.request.urlopen("https://tlogi.xyz/sd_uploader_latest.exe", timeout=10) as r, open(new_path, "wb") as f:
                    shutil.copyfileobj(r, f)

                messagebox.showinfo("Update Downloaded", "The app will now restart to apply the update.")

                bat_script = f"""@echo off
taskkill /PID {pid} /F >nul
timeout /t 4 >nul
del "{exe_path}" >nul 2>&1
move "{new_path}" "{exe_path}"
timeout /t 2 >nul
start "" "{exe_path}"
del "%~f0"
"""
                bat_path = os.path.join(tempfile.gettempdir(), "update_sd_uploader.bat")
                with open(bat_path, "w") as bat_file:
                    bat_file.write(bat_script)

                subprocess.Popen(["cmd", "/c", bat_path], shell=True)
                return
    except Exception as e:
        if not auto:
            messagebox.showerror("Update Failed", f"Could not complete update:\\n{e}")
load_settings()

class UploadApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SD Card Uploader")
        self.root.geometry("300x200")
        self.root.configure(bg="#2e2e2e")

        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background='#2e2e2e', foreground='white', fieldbackground='#3a3a3a')
        style.configure('TButton', background='#444', foreground='white')
        style.configure('TLabel', background='#2e2e2e', foreground='white')
        style.configure('Horizontal.TProgressbar', troughcolor='#444', background='#00ff00')


        self.sd_status_label = ttk.Label(root, text="SD Card: Not Detected")
        self.sd_status_label.pack(pady=5)
        self.settings_button = ttk.Button(root, text="Settings", command=self.open_settings)
        self.settings_button.pack(pady=5)

        self.log_button = ttk.Button(root, text="View Log", command=self.open_log)
        self.log_button.pack(pady=5)

        self.status_label = ttk.Label(root, text="Files Detected: 0 | Uploaded: 0 | Remaining: 0")
        self.status_label.pack(pady=5)

        self.eta_label = ttk.Label(root, text="Estimated Time Remaining: N/A")
        self.eta_label.pack(pady=2)

        self.storage_label = ttk.Label(root, text="Storage: SD=0MB | SMB=∞MB")
        self.storage_label.pack(pady=2)

        self.progress = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
        self.progress.pack(pady=10)
        self.status_label.pack_forget()
        self.eta_label.pack_forget()
        self.storage_label.pack_forget()
        self.progress.pack_forget()

        self.log_lines = []
        self.log_lock = threading.Lock()

        self.monitoring = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()

        self.counters = {'detected': 0, 'uploaded': 0, 'remaining': 0, 'skipped': 0}
        self.start_time = None
        self.tray_icon = None
        self.setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        threading.Thread(target=lambda: check_for_updates(True), daemon=True).start()

    def log(self, message):
        timestamp = f"{datetime.now().strftime('%H:%M:%S')} - {message}"
        with self.log_lock:
            self.log_lines.append(timestamp)
        self.update_status()


    def update_status(self):
        percent = 0
        if self.counters['detected'] > 0:
            percent = (self.counters['uploaded'] / self.counters['detected']) * 100
            self.progress['value'] = percent
            percent_text = f"{percent:.1f}%"
            elapsed = time.time() - self.start_time if self.start_time else 0
            if self.counters['uploaded'] > 0:
                eta = (elapsed / self.counters['uploaded']) * self.counters['remaining']
                eta_text = time.strftime('%M:%S', time.gmtime(eta))
            else:
                eta_text = "Calculating..."
        else:
            self.progress['value'] = 0
            percent_text = "0%"
            eta_text = "N/A"

        self.status_label.config(
            text=f"Files Detected: {self.counters['detected']} | Uploaded: {self.counters['uploaded']} | Skipped: {self.counters['skipped']} | Remaining: {self.counters['remaining']} ({percent_text})"
        )
        self.eta_label.config(text=f"Estimated Time Remaining: {eta_text}")

    def update_storage(self):
        try:
            if os.path.exists(SD_LABEL):
                sd_free = get_local_free_space(SD_LABEL) / (1024 * 1024)
                total_upload_size = get_total_upload_size(SD_LABEL) / (1024 * 1024)
            else:
                sd_free = 0
                total_upload_size = 0
        except Exception:
            sd_free = 0
            total_upload_size = 0

        try:
            smb_path = f"//{SMB_SERVER}/{SMB_SHARE}"
            smb_free = estimate_smb_free_space(smb_path)
            if smb_free == float("inf"):
                smb_free_mb = "Unknown"
            else:
                smb_free_mb = f"{(smb_free / (1024 * 1024)):.1f}MB"
        except Exception:
            smb_free_mb = "Unknown"

        text = f"Storage: SD Free={sd_free:.1f}MB | To Upload={total_upload_size:.1f}MB | SMB Free={smb_free_mb}"
        self.storage_label.config(text=text)

        self.status_label.config(text=f"Files Detected: {self.counters['detected']} | Uploaded: {self.counters['uploaded']} | Skipped: {self.counters['skipped']} | Remaining: {self.counters['remaining']} ({percent_text})")
        if self.counters['detected'] > 0:
            percent = (self.counters['uploaded'] / self.counters['detected']) * 100
            self.progress['value'] = percent
            elapsed = time.time() - self.start_time if self.start_time else 0
            if self.counters['uploaded'] > 0:
                eta = (elapsed / self.counters['uploaded']) * self.counters['remaining']
                self.eta_label.config(text=f"Estimated Time Remaining: {time.strftime('%M:%S', time.gmtime(eta))}")
        else:
            self.progress['value'] = 0
            self.eta_label.config(text="Estimated Time Remaining: N/A")

    def update_storage(self):
        try:
            sd_free = get_local_free_space(SD_LABEL) / (1024 * 1024)
            total_upload_size = get_total_upload_size(SD_LABEL) / (1024 * 1024)
        except Exception:
            sd_free, total_upload_size = 0, 0

        try:
            smb_path = f"//{SMB_SERVER}/{SMB_SHARE}"
            smb_free = estimate_smb_free_space(smb_path) / (1024 * 1024)
        except:
            smb_free = float("inf")

        text = f"Storage: SD Free={sd_free:.1f}MB | To Upload={total_upload_size:.1f}MB | SMB Free={smb_free:.1f}MB"
        self.storage_label.config(text=text)

    def monitor_loop(self):
        self.sd_present = False
        while self.monitoring:
            if os.path.exists(SD_LABEL) and os.path.isdir(SD_LABEL):
                if not self.sd_present:
                    self.sd_present = True
                    self.sd_status_label.config(text="SD Card: Detected")
                    self.status_label.pack(pady=5)
                    self.eta_label.pack(pady=2)
                    self.storage_label.pack(pady=2)
                    self.progress.pack(pady=10)
                    self.log(f"SD card detected at {SD_LABEL}")
                    self.counters.update({'detected': 0, 'uploaded': 0, 'remaining': 0, 'skipped': 0})
                    self.start_time = time.time()
                    upload_files(SD_LABEL, self.log, self.counters, self.update_status, self.update_storage)
                    try:
                        upload_files(SD_LABEL, self.log, self.counters, self.update_status, self.update_storage)
                    except Exception as e:
                        self.log(f"❌ Upload failed: {e}")
                    self.log("✅ Upload complete.")
                    self.update_status()
                    while os.path.exists(SD_LABEL):
                        time.sleep(5)
                    self.log("SD card removed.")
                    self.sd_present = False
                    self.sd_status_label.config(text="SD Card: Not Detected")
                    self.status_label.pack_forget()
                    self.eta_label.pack_forget()
                    self.storage_label.pack_forget()
                    self.progress.pack_forget()
                    self.counters.update({'detected': 0, 'uploaded': 0, 'remaining': 0, 'skipped': 0})
                    self.update_status()
            else:
                time.sleep(5)

    def open_settings(self):
        settings_win = Toplevel(self.root, bg="#2e2e2e")
        settings_win.title("Settings")

        def add_entry(label, var, row):
            ttk.Label(settings_win, text=label).grid(row=row, column=0, sticky='e')
            ttk.Entry(settings_win, textvariable=var).grid(row=row, column=1, sticky='w')

        global SMB_SERVER, SMB_SHARE, SMB_USER, SMB_PASS
        sd_var = StringVar(value=SD_LABEL)
        sd_label_var = StringVar(value=SD_LABEL)
        server_var = StringVar(value=SMB_SERVER)
        share_var = StringVar(value=SMB_SHARE)
        user_var = StringVar(value=SMB_USER)
        pass_var = StringVar(value=SMB_PASS)

        add_entry("SD Label:", sd_var, 0)
        add_entry("SD Label:", sd_label_var, 0)
        add_entry("SMB Server:", server_var, 1)
        add_entry("SMB Share:", share_var, 2)
        add_entry("Username:", user_var, 3)
        add_entry("Password:", pass_var, 4)

        ext_vars = {}
        for i, ext in enumerate(ALLOWED_EXTENSIONS):
            var = BooleanVar(value=ALLOWED_EXTENSIONS[ext])
            Checkbutton(settings_win, text=ext, variable=var, bg="#2e2e2e", fg="white", selectcolor="#444").grid(row=4 + i, columnspan=2, sticky='w')
            ext_vars[ext] = var

        def save_settings():
            globals()['SD_LABEL'] = sd_var.get()
            globals()['SD_LABEL'] = sd_label_var.get()
            globals()['SMB_SERVER'] = server_var.get()
            globals()['SMB_SHARE'] = share_var.get()
            globals()['SMB_USER'] = user_var.get()
            globals()['SMB_PASS'] = pass_var.get()
            for ext, var in ext_vars.items():
                ALLOWED_EXTENSIONS[ext] = var.get()
            response = os.system(f'ping -n 1 {server_var.get()} >nul')
            if response != 0:
                messagebox.showerror("Ping Failed", f"Cannot reach SMB server: {server_var.get()}")
                return
            try:
                smbclient.register_session(server=server_var.get(), username=user_var.get(), password=pass_var.get())
            except Exception as e:
                messagebox.showerror("Connection Failed", f"Failed to connect to SMB server: {e}")
                return
            with open(SETTINGS_FILE, 'w') as f:
                json.dump({
                    'SD_LABEL': sd_var.get(),
                    'SMB_SERVER': server_var.get(),
                    'SMB_SHARE': share_var.get(),
                    'SMB_USER': user_var.get(),
                    'SMB_PASS': pass_var.get(),
                    'ALLOWED_EXTENSIONS': {ext: var.get() for ext, var in ext_vars.items()}
                }, f, indent=4)
            messagebox.showinfo("Settings", "Settings updated.")
            settings_win.destroy()

        def clear_database():
            clear_db()
            messagebox.showinfo("Database", "Photo database cleared.")

        ttk.Label(settings_win, text=f"Version: 1.0.4").grid(row=12, columnspan=2, pady=(10, 0))
        ttk.Button(settings_win, text="Check for Updates", command=check_for_updates).grid(row=13, columnspan=2, pady=5)
        ttk.Button(settings_win, text="Save", command=save_settings).grid(row=10, columnspan=2, pady=10)
        ttk.Button(settings_win, text="Clear Photo Database", command=clear_database).grid(row=11, columnspan=2, pady=5)

    def open_log(self):
        log_win = Toplevel(self.root)
        log_win.title("Log Console")
        log_console = tk.Text(log_win, width=100, height=25, bg="#1e1e1e", fg="white")
        log_console.pack()

        def update_log():
            with self.log_lock:
                log_console.delete("1.0", tk.END)
                log_console.insert(tk.END, "\n".join(self.log_lines))
                log_console.see(tk.END)
            log_win.after(2000, update_log)

        update_log()

    def minimize_to_tray(self):
        self.root.withdraw()
        self.tray_icon.visible = True

    def restore_window(self, icon=None, item=None):
        self.root.deiconify()
        self.tray_icon.visible = False

    def setup_tray(self):
        icon_image = Image.new('RGB', (64, 64), color='white')
        draw = ImageDraw.Draw(icon_image)
        draw.rectangle([16, 16, 48, 48], fill='black')

        menu = pystray.Menu(pystray.MenuItem("Open", self.restore_window), pystray.MenuItem("Quit", self.quit_app))
        self.tray_icon = pystray.Icon("Uploader", icon_image, "SD Uploader", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def quit_app(self):
        self.tray_icon.stop()
        self.root.quit()
        os._exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    root = tk.Tk()
    app = UploadApp(root)
    root.mainloop()
