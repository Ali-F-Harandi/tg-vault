#!/usr/bin/env python3
"""
tg-vault GUI — Professional desktop interface using tkinter.

Features:
  - Upload with encryption & compression
  - Download by manifest link(s)
  - Browse database with filters, sort, multi-select download
  - Database management (sync, restore, vacuum, export)
  - Proxy support (system proxy or custom)
  - All operations run in background threads (non-blocking UI)

Usage:
    python gui.py                    # uses default config
    python gui.py --config /path     # custom config

Requirements:
    - tkinter (built into Python on Windows/macOS)
    - requests
    - cryptography (for encryption)
"""

import os
import sys
import json
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# Import tg.py as a module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg as tgv


# ═══════════════════════════════════════════════════════════════
# Config helpers
# ═══════════════════════════════════════════════════════════════
PROXY_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".proxy_settings.json")


def find_config_path():
    """Find config file: --config flag > config.json (local) > ~/.tg-vault.json"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_config = os.path.join(script_dir, "config.json")
    home_config = os.path.expanduser("~/.tg-vault.json")

    if len(sys.argv) > 2 and sys.argv[1] == "--config":
        return sys.argv[2]
    elif os.path.exists(local_config):
        return local_config
    else:
        return home_config


def load_proxy_settings():
    """Load proxy settings from file."""
    try:
        with open(PROXY_SETTINGS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"use_system_proxy": False, "custom_proxy": ""}


def save_proxy_settings(settings):
    """Save proxy settings to file."""
    with open(PROXY_SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


def build_proxy_env(settings=None):
    """Build environment dict with proxy settings for subprocess."""
    env = os.environ.copy()
    if settings is None:
        settings = load_proxy_settings()

    if settings.get("use_system_proxy"):
        # System proxy: don't override — let requests/urllib pick up
        # system proxy settings automatically (they already do via env vars)
        pass
    elif settings.get("custom_proxy"):
        proxy = settings["custom_proxy"]
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
    else:
        # No proxy: explicitly clear proxy env vars
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
            env.pop(key, None)

    return env


def run_tg_command(config_path, *args, timeout=600, proxy_settings=None, progress_callback=None):
    """Run a tg.py command and return (success, output).

    If progress_callback is provided, it's called with each line of output
    for real-time progress display.
    """
    tg_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg.py")
    cmd = [sys.executable, tg_script, "--config", config_path] + list(args)

    env = build_proxy_env(proxy_settings) if proxy_settings else build_proxy_env()

    # Force UTF-8 for subprocess — Windows console defaults to cp1252
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Log to console
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[tg-vault GUI] Running command:", file=sys.stderr)
    print(f"  Script: {tg_script}", file=sys.stderr)
    print(f"  Config: {config_path}", file=sys.stderr)
    print(f"  Args:   {' '.join(str(a) for a in args)}", file=sys.stderr)
    print(f"  Full:   {' '.join(str(c) for c in cmd)}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if not os.path.exists(tg_script):
        msg = f"tg.py not found at: {tg_script}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

    if not os.path.exists(config_path):
        msg = f"Config file not found: {config_path}\nRun setup first or create config.json"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
            encoding='utf-8', errors='replace'
        )

        output_lines = []
        try:
            for line in iter(process.stdout.readline, ''):
                output_lines.append(line)
                print(line, end='', file=sys.stderr)  # Log to console
                if progress_callback:
                    progress_callback(line.rstrip('\n\r'))
        finally:
            process.stdout.close()
            process.wait(timeout=timeout)

        output = ''.join(output_lines)
        print(f"[tg-vault GUI] Exit code: {process.returncode}", file=sys.stderr)

        if not output.strip():
            output = f"(no output — exit code {process.returncode})\nCommand: tg.py {' '.join(str(a) for a in args)}"

        return process.returncode == 0, output

    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout} seconds"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg
    except FileNotFoundError as e:
        msg = f"Python executable not found: {sys.executable}\n{e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        msg = f"Error running command:\n{type(e).__name__}: {e}\n\nTraceback:\n{tb}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg


# ═══════════════════════════════════════════════════════════════
# Main Application
# ═══════════════════════════════════════════════════════════════
class TgVaultApp:
    def __init__(self, root):
        self.root = root
        self.config_path = find_config_path()
        self.config = tgv.Config.load(self.config_path)
        self.proxy_settings = load_proxy_settings()

        self.root.title("tg-vault — Telegram Cloud Storage")
        self.root.geometry("900x700")
        self.root.minsize(750, 550)

        # Style
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except:
            pass

        style.configure('Header.TLabel', font=('Segoe UI', 14, 'bold'))
        style.configure('Title.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('Status.TLabel', font=('Segoe UI', 9))
        style.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'))

        # Build UI
        self._build_menu()
        self._build_notebook()
        self._build_status_bar()

        # Load initial data
        self.refresh_status()

    # ─────────────── Menu Bar ───────────────
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Reload Config", command=self.reload_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        db_menu = tk.Menu(menubar, tearoff=0)
        db_menu.add_command(label="Sync DB to Telegram", command=self.action_db_sync)
        db_menu.add_command(label="Restore DB from Telegram", command=self.action_db_restore)
        db_menu.add_command(label="Vacuum DB", command=self.action_db_vacuum)
        db_menu.add_command(label="Export to JSON", command=self.action_db_export)
        menubar.add_cascade(label="Database", menu=db_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ─────────────── Notebook ───────────────
    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self.upload_frame = ttk.Frame(self.notebook)
        self.download_frame = ttk.Frame(self.notebook)
        self.browse_frame = ttk.Frame(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.upload_frame, text="  📤 Upload  ")
        self.notebook.add(self.download_frame, text="  📥 Download  ")
        self.notebook.add(self.browse_frame, text="  🗄️ Browse  ")
        self.notebook.add(self.settings_frame, text="  ⚙️ Settings  ")

        self._build_upload_tab()
        self._build_download_tab()
        self._build_browse_tab()
        self._build_settings_tab()

    # ─────────────── Status Bar ───────────────
    def _build_status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=8, pady=4)

        self.status_label = ttk.Label(self.status_frame, text="Ready", style='Status.TLabel')
        self.status_label.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(self.status_frame, mode='determinate', length=200)
        self.progress.pack(side=tk.RIGHT)

    # ════════════════════════════════════════════════════════════
    # UPLOAD TAB
    # ════════════════════════════════════════════════════════════
    def _build_upload_tab(self):
        frame = self.upload_frame

        ttk.Label(frame, text="Upload Files", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        # File selection
        file_frame = ttk.LabelFrame(frame, text="File Selection", padding=12)
        file_frame.pack(fill=tk.X, padx=16, pady=4)

        self.upload_filepaths = tk.StringVar()
        row = ttk.Frame(file_frame)
        row.pack(fill=tk.X)
        ttk.Entry(row, textvariable=self.upload_filepaths, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse...", command=self.browse_files).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row, text="Clear", command=lambda: self.upload_filepaths.set("")).pack(side=tk.LEFT, padx=(4, 0))

        self.file_count_label = ttk.Label(file_frame, text="No files selected", style='Status.TLabel')
        self.file_count_label.pack(anchor=tk.W, pady=(4, 0))

        # Details
        details_frame = ttk.LabelFrame(frame, text="Details (optional)", padding=12)
        details_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(details_frame, text="Description:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.upload_desc = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.upload_desc, width=60).grid(row=0, column=1, sticky=tk.EW, pady=2, padx=(8, 0))

        ttk.Label(details_frame, text="Hashtags:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.upload_tags = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.upload_tags, width=60).grid(row=1, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        ttk.Label(details_frame, text="(comma-separated, e.g. manga,2026,backup)", style='Status.TLabel').grid(row=2, column=1, sticky=tk.W, padx=(8, 0))

        details_frame.columnconfigure(1, weight=1)

        # Options
        opts_frame = ttk.LabelFrame(frame, text="Options", padding=12)
        opts_frame.pack(fill=tk.X, padx=16, pady=4)

        self.upload_encrypt = tk.BooleanVar(value=False)
        self.upload_compress = tk.BooleanVar(value=True)
        self.upload_password = tk.StringVar()

        ttk.Checkbutton(opts_frame, text="🔐 Encrypt (AES-256-GCM)", variable=self.upload_encrypt, command=self._toggle_password).grid(row=0, column=0, sticky=tk.W, padx=(0, 20))
        ttk.Checkbutton(opts_frame, text="📦 Compress (gzip)", variable=self.upload_compress).grid(row=0, column=1, sticky=tk.W)

        self.pwd_label = ttk.Label(opts_frame, text="Password:")
        self.pwd_entry = ttk.Entry(opts_frame, textvariable=self.upload_password, show="•", state='disabled', width=30)
        self.pwd_label.grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.pwd_entry.grid(row=1, column=1, sticky=tk.W, pady=(8, 0))

        # Upload button
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        top_row = ttk.Frame(btn_frame)
        top_row.pack(fill=tk.X)
        self.upload_btn = ttk.Button(top_row, text="🚀 Upload", style='Accent.TButton', command=self.do_upload)
        self.upload_btn.pack(side=tk.LEFT)

        self.upload_result = tk.Text(btn_frame, height=8, state='disabled', wrap=tk.WORD)
        self.upload_result.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _toggle_password(self):
        if self.upload_encrypt.get():
            self.pwd_entry.config(state='normal')
        else:
            self.pwd_entry.config(state='disabled')
            self.upload_password.set("")

    def browse_files(self):
        files = filedialog.askopenfilenames(title="Select files to upload")
        if files:
            self.upload_filepaths.set(" ; ".join(files))
            self.file_count_label.config(text=f"{len(files)} file(s) selected")

    def do_upload(self):
        paths = self.upload_filepaths.get().strip()
        if not paths:
            messagebox.showwarning("No files", "Please select files first.")
            return

        file_list = [p.strip() for p in paths.split(";") if p.strip()]
        desc = self.upload_desc.get().strip()
        tags = self.upload_tags.get().strip()
        encrypt = self.upload_encrypt.get()
        compress = self.upload_compress.get()
        password = self.upload_password.get()

        if encrypt and not password:
            messagebox.showwarning("Password required", "Please enter a password for encryption.")
            return

        msg = f"Upload {len(file_list)} file(s)?"
        if encrypt:
            msg += "\n\n🔐 Encryption: ENABLED"
        if not compress:
            msg += "\n📦 Compression: DISABLED"
        if not messagebox.askyesno("Confirm Upload", msg):
            return

        args = ["upload"] + file_list
        if desc:
            args.extend(["--desc", desc])
        if tags:
            args.extend(["--tag", tags])
        if encrypt:
            args.append("--encrypt")
            args.extend(["--password", password])
        if not compress:
            args.append("--no-compress")

        # Clear output box before starting
        self.upload_result.config(state='normal')
        self.upload_result.delete(1.0, tk.END)
        self.upload_result.config(state='disabled')

        self._run_async("Uploading...", args, callback=self._on_upload_done, progress_callback=self._on_upload_progress)

    def _on_upload_progress(self, line):
        """Called for each line of upload output — show in text box."""
        self.upload_result.config(state='normal')
        self.upload_result.insert(tk.END, line + "\n")
        self.upload_result.see(tk.END)
        self.upload_result.config(state='disabled')

    def _on_upload_done(self, success, output):
        # Don't clear — progress already wrote line by line
        self.upload_result.config(state='normal')
        if success:
            self.upload_result.insert(tk.END, "\n✅ Done!\n")
        else:
            self.upload_result.insert(tk.END, f"\n❌ FAILED\n")
        self.upload_result.see(tk.END)
        self.upload_result.config(state='disabled')
        if not success:
            messagebox.showerror("Upload Failed", output[-800:])

    # ════════════════════════════════════════════════════════════
    # DOWNLOAD TAB
    # ════════════════════════════════════════════════════════════
    def _build_download_tab(self):
        frame = self.download_frame

        ttk.Label(frame, text="Download Files", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        link_frame = ttk.LabelFrame(frame, text="Manifest Link(s)", padding=12)
        link_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(link_frame, text="Enter one or more t.me/ links (one per line):").pack(anchor=tk.W)
        self.download_links = tk.Text(link_frame, height=4, wrap=tk.WORD)
        self.download_links.pack(fill=tk.X, pady=(4, 0))

        opt_frame = ttk.LabelFrame(frame, text="Options", padding=12)
        opt_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(opt_frame, text="Password (for encrypted files):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.download_password = tk.StringVar()
        ttk.Entry(opt_frame, textvariable=self.download_password, show="•", width=30).grid(row=0, column=1, sticky=tk.W, pady=2, padx=(8, 0))

        ttk.Label(opt_frame, text="Output directory:").grid(row=1, column=0, sticky=tk.W, pady=2)
        out_row = ttk.Frame(opt_frame)
        out_row.grid(row=1, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        self.download_outdir = tk.StringVar(value=os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads"))
        ttk.Entry(out_row, textvariable=self.download_outdir).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(out_row, text="Browse...", command=self._browse_outdir).pack(side=tk.LEFT, padx=(4, 0))

        opt_frame.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=16, pady=12)
        ttk.Button(btn_frame, text="📥 Download", style='Accent.TButton', command=self.do_download).pack(side=tk.LEFT)

        self.download_result = tk.Text(frame, height=10, state='disabled', wrap=tk.WORD)
        self.download_result.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

    def _browse_outdir(self):
        d = filedialog.askdirectory(title="Select output directory")
        if d:
            self.download_outdir.set(d)

    def do_download(self):
        links_text = self.download_links.get(1.0, tk.END).strip()
        if not links_text:
            messagebox.showwarning("No links", "Please enter at least one link.")
            return

        links = [l.strip() for l in links_text.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not links:
            messagebox.showwarning("No links", "No valid links found.")
            return

        password = self.download_password.get().strip()
        out_dir = self.download_outdir.get().strip() or "."

        args = ["download"] + links
        args.extend(["--output-dir", out_dir])
        if password:
            args.extend(["--password", password])

        # Clear output box before starting
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.config(state='disabled')

        self._run_async(f"Downloading {len(links)} file(s)...", args, callback=self._on_download_done, progress_callback=self._on_download_progress)

    def _on_download_progress(self, line):
        """Called for each line of download output — show in text box."""
        self.download_result.config(state='normal')
        self.download_result.insert(tk.END, line + "\n")
        self.download_result.see(tk.END)
        self.download_result.config(state='disabled')

    def _on_download_done(self, success, output):
        # Don't clear — progress already wrote line by line
        self.download_result.config(state='normal')
        if success:
            self.download_result.insert(tk.END, "\n✅ Done!\n")
        else:
            self.download_result.insert(tk.END, f"\n❌ FAILED\n")
        self.download_result.see(tk.END)
        self.download_result.config(state='disabled')
        if not success:
            messagebox.showerror("Download Failed", output[-800:])

    # ════════════════════════════════════════════════════════════
    # BROWSE TAB
    # ════════════════════════════════════════════════════════════
    def _build_browse_tab(self):
        frame = self.browse_frame

        ttk.Label(frame, text="Browse Database", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        # Filter bar
        filter_frame = ttk.LabelFrame(frame, text="Filters", padding=10)
        filter_frame.pack(fill=tk.X, padx=16, pady=4)

        row1 = ttk.Frame(filter_frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="Name:").pack(side=tk.LEFT)
        self.f_name = tk.StringVar()
        ttk.Entry(row1, textvariable=self.f_name, width=20).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row1, text="Tag:").pack(side=tk.LEFT)
        self.f_tag = tk.StringVar()
        ttk.Entry(row1, textvariable=self.f_tag, width=15).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row1, text="Min MB:").pack(side=tk.LEFT)
        self.f_min_size = tk.StringVar()
        ttk.Entry(row1, textvariable=self.f_min_size, width=6).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(row1, text="Max MB:").pack(side=tk.LEFT)
        self.f_max_size = tk.StringVar()
        ttk.Entry(row1, textvariable=self.f_max_size, width=6).pack(side=tk.LEFT, padx=(4, 12))

        row2 = ttk.Frame(filter_frame)
        row2.pack(fill=tk.X, pady=2)

        self.f_encrypted = tk.BooleanVar()
        self.f_compressed = tk.BooleanVar()
        ttk.Checkbutton(row2, text="🔐 Encrypted only", variable=self.f_encrypted).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(row2, text="📦 Compressed only", variable=self.f_compressed).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row2, text="Sort:").pack(side=tk.LEFT, padx=(12, 4))
        self.f_sort = tk.StringVar(value="date")
        ttk.Combobox(row2, textvariable=self.f_sort, values=["date", "name", "size", "parts", "downloads"], width=10, state='readonly').pack(side=tk.LEFT, padx=(0, 4))

        self.f_sort_dir = tk.StringVar(value="desc")
        ttk.Combobox(row2, textvariable=self.f_sort_dir, values=["desc", "asc"], width=5, state='readonly').pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(row2, text="🔍 Search", command=self.refresh_file_list).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(row2, text="Reset", command=self.reset_filters).pack(side=tk.LEFT, padx=(4, 0))

        # Treeview
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        columns = ("id", "name", "size", "parts", "enc", "date", "link")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', selectmode='extended')

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("parts", text="Parts")
        self.tree.heading("enc", text="🔒")
        self.tree.heading("date", text="Date")
        self.tree.heading("link", text="Link")

        self.tree.column("id", width=40, anchor='center')
        self.tree.column("name", width=200)
        self.tree.column("size", width=80, anchor='e')
        self.tree.column("parts", width=50, anchor='center')
        self.tree.column("enc", width=30, anchor='center')
        self.tree.column("date", width=140)
        self.tree.column("link", width=200)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Action buttons
        action_frame = ttk.Frame(frame)
        action_frame.pack(fill=tk.X, padx=16, pady=(0, 16))

        ttk.Button(action_frame, text="📥 Download Selected", command=self.download_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📥 Download All Matching", command=self.download_all_matching).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📋 Copy Link", command=self.copy_link).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🔄 Refresh", command=self.refresh_file_list).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🔍 Find Orphans", command=self.find_orphans).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🗑️ Delete Selected", command=self.delete_selected).pack(side=tk.LEFT, padx=(0, 4))

        self.result_count_label = ttk.Label(action_frame, text="", style='Status.TLabel')
        self.result_count_label.pack(side=tk.RIGHT)

        self.tree.bind("<Double-1>", lambda e: self.download_selected())

        # Right-click context menu
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="📥 Download", command=self.download_selected)
        self.context_menu.add_command(label="📋 Copy Link", command=self.copy_link)
        self.context_menu.add_command(label="📋 Copy Name", command=self.copy_name)
        self.context_menu.add_command(label="📋 Copy ID", command=self.copy_id)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📋 Copy All Links (Selected)", command=self.copy_all_links)

        def on_right_click(event):
            item = self.tree.identify_row(event.y)
            if item:
                if item not in self.tree.selection():
                    self.tree.selection_set(item)
                self.context_menu.tk_popup(event.x_root, event.y_root)

        def on_copy_shortcut(event):
            self.copy_link()

        self.tree.bind("<Button-3>", on_right_click)  # Right-click
        self.tree.bind("<Control-c>", on_copy_shortcut)  # Ctrl+C

    def get_filters(self):
        filters = {}
        if self.f_name.get().strip():
            filters["--name"] = self.f_name.get().strip()
        if self.f_tag.get().strip():
            filters["--tag"] = self.f_tag.get().strip()
        if self.f_min_size.get().strip():
            try:
                filters["--min-size"] = str(int(float(self.f_min_size.get()) * 1048576))
            except ValueError:
                pass
        if self.f_max_size.get().strip():
            try:
                filters["--max-size"] = str(int(float(self.f_max_size.get()) * 1048576))
            except ValueError:
                pass
        if self.f_encrypted.get():
            filters["--encrypted"] = ""
        if self.f_compressed.get():
            filters["--compressed"] = ""
        filters["--sort"] = self.f_sort.get()
        if self.f_sort_dir.get() == "asc":
            filters["--asc"] = ""
        return filters

    def reset_filters(self):
        self.f_name.set("")
        self.f_tag.set("")
        self.f_min_size.set("")
        self.f_max_size.set("")
        self.f_encrypted.set(False)
        self.f_compressed.set(False)
        self.f_sort.set("date")
        self.f_sort_dir.set("desc")
        self.refresh_file_list()

    def refresh_file_list(self):
        db = self.config.get_db()
        if db is None:
            messagebox.showwarning("Database not enabled", "Please enable the database in Settings.")
            return

        filters = {}
        if self.f_name.get().strip():
            filters["name"] = self.f_name.get().strip()
        if self.f_tag.get().strip():
            filters["tag"] = self.f_tag.get().strip()
        if self.f_min_size.get().strip():
            try:
                filters["min_size"] = int(float(self.f_min_size.get()) * 1048576)
            except ValueError:
                pass
        if self.f_max_size.get().strip():
            try:
                filters["max_size"] = int(float(self.f_max_size.get()) * 1048576)
            except ValueError:
                pass
        if self.f_encrypted.get():
            filters["encrypted"] = True
        if self.f_compressed.get():
            filters["compressed"] = True
        filters["sort"] = self.f_sort.get()
        filters["sort_dir"] = self.f_sort_dir.get()
        filters["limit"] = 500

        rows = db.query_files(filters)
        count = db.count_files(filters)

        for item in self.tree.get_children():
            self.tree.delete(item)

        for r in rows:
            size_str = tgv.format_size(r["size"])
            enc_str = "🔐" if r.get("encrypted") else ""
            date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["uploaded_at"]))
            link = r.get("share_link") or ""
            self.tree.insert("", tk.END, values=(r["id"], r["name"], size_str, r["total_parts"], enc_str, date_str, link))

        self.result_count_label.config(text=f"{len(rows)} of {count} files")

    def download_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more files.")
            return

        ids = []
        for item in selected:
            vals = self.tree.item(item, "values")
            ids.append(vals[0])

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        if len(ids) == 1:
            args = ["db", "download", ids[0], "--output-dir", out_dir]
        else:
            args = ["db", "download", "--ids", ",".join(ids), "--output-dir", out_dir]

        # Switch to Download tab and show progress there too
        self.notebook.select(1)  # Switch to Download tab
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.config(state='disabled')
        self._run_async(f"Downloading {len(ids)} file(s) from DB...", args,
                        callback=self._on_download_done,
                        progress_callback=self._on_download_progress)

    def download_all_matching(self):
        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        filters = self.get_filters()
        args = ["db", "download", "--all-matching", "--output-dir", out_dir]
        for k, v in filters.items():
            args.append(k)
            if v:
                args.append(v)

        # Switch to Download tab and show progress there too
        self.notebook.select(1)  # Switch to Download tab
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.config(state='disabled')
        self._run_async("Downloading all matching files from DB...", args,
                        callback=self._on_download_done,
                        progress_callback=self._on_download_progress)

    def _on_browse_download_done(self, success, output):
        if success:
            messagebox.showinfo("Download Complete", output[-500:])
        else:
            messagebox.showerror("Download Failed", output[-800:])

    def copy_link(self):
        selected = self.tree.selection()
        if not selected:
            return
        vals = self.tree.item(selected[0], "values")
        link = vals[6]
        if link:
            self.root.clipboard_clear()
            self.root.clipboard_append(link)
            self.status_label.config(text=f"Copied: {link}")

    def copy_name(self):
        selected = self.tree.selection()
        if not selected:
            return
        vals = self.tree.item(selected[0], "values")
        name = vals[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(name)
        self.status_label.config(text=f"Copied: {name}")

    def copy_id(self):
        selected = self.tree.selection()
        if not selected:
            return
        vals = self.tree.item(selected[0], "values")
        id_val = vals[0]
        self.root.clipboard_clear()
        self.root.clipboard_append(id_val)
        self.status_label.config(text=f"Copied: ID {id_val}")

    def copy_all_links(self):
        selected = self.tree.selection()
        if not selected:
            return
        links = []
        for item in selected:
            vals = self.tree.item(item, "values")
            link = vals[6]
            if link:
                links.append(link)
        if links:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(links))
            self.status_label.config(text=f"Copied {len(links)} link(s)")

    def find_orphans(self):
        """Scan main channel for manifest messages not in database."""
        if not messagebox.askyesno("Find Orphans",
            "This will scan the main channel for files not in the database.\n\n"
            "It may take a few minutes. Continue?"):
            return

        # Switch to Download tab to show progress
        self.notebook.select(1)
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.insert(tk.END, "🔍 Scanning main channel for orphaned files...\n")
        self.download_result.config(state='disabled')

        def progress_cb(line):
            self.download_result.config(state='normal')
            self.download_result.insert(tk.END, line + "\n")
            self.download_result.see(tk.END)
            self.download_result.config(state='disabled')

        def done_cb(success, output):
            self.download_result.config(state='normal')
            self.download_result.insert(tk.END, "\n" + output[-500:] if not success else "\n✅ Done!\n")
            self.download_result.see(tk.END)
            self.download_result.config(state='disabled')
            if not success:
                messagebox.showerror("Find Orphans Failed", output[-500:])

        self._run_async("Finding orphans...", ["db", "find-orphans"],
                        callback=done_cb, progress_callback=progress_cb)

    def delete_selected(self):
        """Delete selected files from both Telegram and database."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more files to delete.")
            return

        ids = []
        names = []
        for item in selected:
            vals = self.tree.item(item, "values")
            ids.append(vals[0])
            names.append(vals[1])

        msg = f"Delete {len(ids)} file(s) from Telegram AND database?\n\n"
        for n in names[:5]:
            msg += f"  • {n}\n"
        if len(names) > 5:
            msg += f"  ... and {len(names) - 5} more\n"
        msg += "\nThis cannot be undone!"

        if not messagebox.askyesno("Confirm Delete", msg, icon='warning'):
            return

        # Switch to Download tab to show progress
        self.notebook.select(1)
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.insert(tk.END, f"🗑️ Deleting {len(ids)} file(s)...\n")
        self.download_result.config(state='disabled')

        def progress_cb(line):
            self.download_result.config(state='normal')
            self.download_result.insert(tk.END, line + "\n")
            self.download_result.see(tk.END)
            self.download_result.config(state='disabled')

        def done_cb(success, output):
            self.download_result.config(state='normal')
            if success:
                self.download_result.insert(tk.END, "\n✅ Done!\n")
            else:
                self.download_result.insert(tk.END, f"\n❌ FAILED\n")
            self.download_result.see(tk.END)
            self.download_result.config(state='disabled')
            self.refresh_file_list()
            if not success:
                messagebox.showerror("Delete Failed", output[-500:])

        # Use --force to skip interactive confirmation (we already confirmed in GUI)
        if len(ids) == 1:
            args = ["db", "delete", ids[0], "--force"]
        else:
            # Delete one by one
            args = ["db", "delete", ids[0], "--force"]
            for id_val in ids[1:]:
                args = ["db", "delete", id_val, "--force"]
                self._run_async(f"Deleting file {id_val}...", args,
                                callback=done_cb, progress_callback=progress_cb)
                return

        self._run_async(f"Deleting {len(ids)} file(s)...", args,
                        callback=done_cb, progress_callback=progress_cb)

    # ════════════════════════════════════════════════════════════
    # SETTINGS TAB
    # ════════════════════════════════════════════════════════════
    def _build_settings_tab(self):
        frame = self.settings_frame

        ttk.Label(frame, text="Settings & Configuration", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        # ─── Proxy Section ───
        proxy_frame = ttk.LabelFrame(frame, text="🌐 Network / Proxy", padding=12)
        proxy_frame.pack(fill=tk.X, padx=16, pady=4)

        self.use_system_proxy = tk.BooleanVar(value=self.proxy_settings.get("use_system_proxy", False))
        ttk.Checkbutton(proxy_frame, text="Use system proxy (Windows/IE settings)",
                        variable=self.use_system_proxy,
                        command=self._on_proxy_change).pack(anchor=tk.W)

        ttk.Label(proxy_frame, text="Custom proxy (overrides system proxy):").pack(anchor=tk.W, pady=(8, 2))
        proxy_row = ttk.Frame(proxy_frame)
        proxy_row.pack(fill=tk.X)
        self.custom_proxy = tk.StringVar(value=self.proxy_settings.get("custom_proxy", ""))
        ttk.Entry(proxy_row, textvariable=self.custom_proxy, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(proxy_row, text="Save", command=self._save_proxy).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(proxy_frame, text="Examples: socks5://127.0.0.1:1080  │  http://127.0.0.1:8080  │  (leave empty = no proxy)",
                  style='Status.TLabel').pack(anchor=tk.W, pady=(2, 0))

        # ─── Config Info ───
        info_frame = ttk.LabelFrame(frame, text="Current Configuration", padding=12)
        info_frame.pack(fill=tk.X, padx=16, pady=4)

        self.config_text = tk.Text(info_frame, height=8, wrap=tk.WORD)
        self.config_text.pack(fill=tk.X)
        self._show_config()

        ttk.Button(info_frame, text="Reload", command=self._show_config).pack(anchor=tk.W, pady=(4, 0))

        # ─── Bot Management ───
        bot_frame = ttk.LabelFrame(frame, text="Bot Management", padding=12)
        bot_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(bot_frame, text="Bot Token:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.new_token = tk.StringVar()
        ttk.Entry(bot_frame, textvariable=self.new_token, width=50, show="•").grid(row=0, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        ttk.Button(bot_frame, text="Add Bot", command=self.add_bot).grid(row=0, column=2, padx=(4, 0))

        bot_frame.columnconfigure(1, weight=1)

        # ─── Actions ───
        action_frame = ttk.LabelFrame(frame, text="Actions", padding=12)
        action_frame.pack(fill=tk.X, padx=16, pady=4)

        row = ttk.Frame(action_frame)
        row.pack(fill=tk.X, pady=2)
        ttk.Button(row, text="🔌 Test Connection", command=self.test_connection).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="☁️ Sync DB", command=self.action_db_sync).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="📥 Restore DB", command=self.action_db_restore).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="🧹 Vacuum DB", command=self.action_db_vacuum).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="💾 Export JSON", command=self.action_db_export).pack(side=tk.LEFT, padx=(0, 4))

        row2 = ttk.Frame(action_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Button(row2, text="🗄️ Enable Database", command=self.enable_db).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="🚫 Disable Database", command=self.disable_db).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="🔧 Run Setup Wizard", command=self.run_setup).pack(side=tk.LEFT, padx=(0, 4))

        # ─── Output ───
        ttk.Label(frame, text="Output:", style='Title.TLabel').pack(anchor=tk.W, padx=16, pady=(8, 4))
        self.settings_output = tk.Text(frame, height=6, state='disabled', wrap=tk.WORD)
        self.settings_output.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

    def _on_proxy_change(self):
        """When system proxy checkbox changes, save immediately."""
        self.proxy_settings["use_system_proxy"] = self.use_system_proxy.get()
        save_proxy_settings(self.proxy_settings)
        if self.use_system_proxy.get():
            self.status_label.config(text="Proxy: Using system proxy")
        else:
            self.status_label.config(text="Proxy: Disabled (or custom)")

    def _save_proxy(self):
        """Save custom proxy setting."""
        self.proxy_settings["custom_proxy"] = self.custom_proxy.get().strip()
        save_proxy_settings(self.proxy_settings)
        proxy = self.proxy_settings["custom_proxy"]
        if proxy:
            self.status_label.config(text=f"Proxy saved: {proxy}")
        else:
            self.status_label.config(text="Proxy cleared")

    def _show_config(self):
        self.config = tgv.Config.load(self.config_path)
        try:
            with open(self.config_path, 'r') as f:
                content = f.read()
            import re
            content = re.sub(r'"token":\s*"[^"]*"', '"token": "***MASKED***"', content)
        except FileNotFoundError:
            content = f"Config file not found at: {self.config_path}\n\nRun setup first or create config.json"
        self.config_text.delete(1.0, tk.END)
        self.config_text.insert(tk.END, content)

    def add_bot(self):
        token = self.new_token.get().strip()
        if not token:
            return
        self._run_async("Adding bot...", ["bots", "add", token], callback=self._on_settings_action)

    def test_connection(self):
        self._run_async("Testing connection...", ["test"], callback=self._on_settings_action)

    def action_db_sync(self):
        self._run_async("Syncing DB to Telegram...", ["db", "sync"], callback=self._on_settings_action)

    def action_db_restore(self):
        if not messagebox.askyesno("Restore DB", "This will replace your local DB with the one from Telegram. Continue?"):
            return
        self._run_async("Restoring DB from Telegram...", ["db", "restore"], callback=self._on_settings_action)

    def action_db_vacuum(self):
        self._run_async("Vacuuming DB...", ["db", "vacuum"], callback=self._on_settings_action)

    def action_db_export(self):
        filename = filedialog.asksaveasfilename(title="Export to JSON", defaultextension=".json",
                                                  filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not filename:
            return
        self._run_async("Exporting...", ["db", "export", "-o", filename], callback=self._on_settings_action)

    def enable_db(self):
        self._run_async("Enabling database...", ["db", "enable"], callback=self._on_settings_action)

    def disable_db(self):
        self._run_async("Disabling database...", ["db", "disable"], callback=self._on_settings_action)

    def run_setup(self):
        if messagebox.askyesno("Setup Wizard", "This will open a terminal window for the setup wizard. Continue?\n\nThe GUI will pause until setup is done."):
            self.status_label.config(text="Running setup wizard in terminal...")
            tg_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg.py")
            cmd = [sys.executable, tg_script, "--config", self.config_path, "setup"]
            try:
                subprocess.run(cmd)
                self._show_config()
                self.refresh_status()
                self.status_label.config(text="Setup complete")
            except Exception as e:
                messagebox.showerror("Setup failed", str(e))

    def _on_settings_action(self, success, output):
        self.settings_output.config(state='normal')
        self.settings_output.delete(1.0, tk.END)
        self.settings_output.insert(tk.END, output[-1000:])
        self.settings_output.config(state='disabled')
        self._show_config()
        self.refresh_status()
        self.refresh_file_list()
        if not success:
            messagebox.showerror("Action Failed", output[-800:])

    # ════════════════════════════════════════════════════════════
    # Async runner
    # ════════════════════════════════════════════════════════════
    def _run_async(self, status_text, args, callback=None, progress_callback=None):
        """Run a tg.py command in a background thread.

        Args:
            status_text: Text to show in status bar.
            args: List of string arguments for tg.py (e.g. ["upload", "file.zip"]).
            callback: Function called with (success, output) when done.
            progress_callback: Function called with each line of output for live progress.
        """
        self.status_label.config(text=status_text)
        self.progress.start(10)
        if hasattr(self, 'upload_btn'):
            self.upload_btn.config(state='disabled')

        def worker():
            success, output = run_tg_command(
                self.config_path, *args,
                proxy_settings=self.proxy_settings,
                progress_callback=progress_callback
            )
            self.root.after(0, lambda: self._on_async_done(success, output, callback))

        threading.Thread(target=worker, daemon=True).start()

    def _on_async_done(self, success, output, callback):
        self.progress.stop()
        if success:
            self.status_label.config(text="✅ Ready")
        else:
            self.status_label.config(text="❌ Error — see output below")
            # Always show error in a popup so user can see it
            if output and output.strip():
                # Show in a popup — don't let it disappear silently
                pass  # callback will handle showing in text box
            else:
                output = "Error: No output produced.\n\nCheck the console (terminal) for details.\nMake sure you ran gui.py from a terminal to see logs."

        # Also print to console
        print(f"\n[tg-vault GUI] Command finished: {'SUCCESS' if success else 'FAILED'}", file=sys.stderr)
        if output:
            print(f"[tg-vault GUI] Output ({len(output)} chars):", file=sys.stderr)
            print(output[:2000], file=sys.stderr)
        else:
            print(f"[tg-vault GUI] WARNING: output is empty!", file=sys.stderr)

        if hasattr(self, 'upload_btn'):
            self.upload_btn.config(state='normal')
        if callback:
            callback(success, output)

    # ════════════════════════════════════════════════════════════
    # Misc
    # ════════════════════════════════════════════════════════════
    def refresh_status(self):
        self.config = tgv.Config.load(self.config_path)
        db_status = "✅" if self.config.db_enabled else "❌"
        bot_count = len(self.config.bots)
        proxy_info = ""
        if self.proxy_settings.get("use_system_proxy"):
            proxy_info = " | Proxy: System"
        elif self.proxy_settings.get("custom_proxy"):
            proxy_info = f" | Proxy: Custom"
        self.root.title(f"tg-vault — Telegram Cloud Storage | Bots: {bot_count} | DB: {db_status}{proxy_info}")

    def reload_config(self):
        self._show_config()
        self.refresh_status()
        self.refresh_file_list()
        self.status_label.config(text="Config reloaded")

    def show_about(self):
        messagebox.showinfo("About tg-vault",
            "tg-vault v8 — Telegram Cloud Storage\n\n"
            "Use Telegram as personal cloud storage with only Bot API tokens.\n"
            "No phone number, no api_id/api_hash, no MTProto required.\n\n"
            "Features:\n"
            "• Multi-bot support\n"
            "• AES-256-GCM encryption\n"
            "• Smart gzip compression\n"
            "• SQLite database with search & filters\n"
            "• Resume for upload & download\n"
            "• DB backup sync to Telegram\n"
            "• Proxy support\n\n"
            "MIT License")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    app = TgVaultApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
