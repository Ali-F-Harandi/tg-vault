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
    python gui/app.py                # uses default config
    python gui/app.py --config /path # custom config
    # or via the root shim:
    python gui.py                    # uses default config

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

# Make the tg_vault package importable when running gui/app.py directly.
# Also resolve the project root (parent of gui/) so we can find tg.py and config.json.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import tg_vault as tgv


def _resolve_tg_script():
    """Find the tg.py shim to invoke.

    Priority:
      1. <project_root>/tg.py  (the backward-compat shim — preferred)
      2. <project_root>/tg_vault/__main__.py  (fallback if shim is absent)
      3. use ``python -m tg_vault``  (last resort — return the literal string "-m tg_vault")
    """
    root_tg = os.path.join(_PROJECT_ROOT, "tg.py")
    if os.path.exists(root_tg):
        return root_tg
    main_mod = os.path.join(_PROJECT_ROOT, "tg_vault", "__main__.py")
    if os.path.exists(main_mod):
        return main_mod
    # Fall back to module mode (no script file needed)
    return None


# ═══════════════════════════════════════════════════════════════
# Config helpers
# ═══════════════════════════════════════════════════════════════
# Place proxy-settings file at the project root, not inside the gui/ dir,
# so that running gui.py from the project root works the same as before.
PROXY_SETTINGS_FILE = os.path.join(_PROJECT_ROOT, ".proxy_settings.json")


def find_config_path():
    """Find config file: --config flag > config.json (project root) > ~/.tg-vault.json"""
    local_config = os.path.join(_PROJECT_ROOT, "config.json")
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
    """Run a tg-vault command and return (success, output).

    If progress_callback is provided, it's called with each line of output
    for real-time progress display.
    """
    tg_script = _resolve_tg_script()
    # Build the command. If we have a script file (tg.py or __main__.py),
    # invoke it directly; otherwise fall back to ``python -m tg_vault``.
    if tg_script is not None:
        cmd = [sys.executable, tg_script, "--config", config_path] + list(args)
    else:
        cmd = [sys.executable, "-m", "tg_vault", "--config", config_path] + list(args)

    env = build_proxy_env(proxy_settings) if proxy_settings else build_proxy_env()

    # Force UTF-8 for subprocess — Windows console defaults to cp1252
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Log to console
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[tg-vault GUI] Running command:", file=sys.stderr)
    print(f"  Script: {tg_script or '-m tg_vault'}", file=sys.stderr)
    print(f"  Config: {config_path}", file=sys.stderr)
    print(f"  Args:   {' '.join(str(a) for a in args)}", file=sys.stderr)
    print(f"  Full:   {' '.join(str(c) for c in cmd)}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if tg_script is not None and not os.path.exists(tg_script):
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
            env=env, cwd=_PROJECT_ROOT,
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

        # Track scrollable canvases for mouse-wheel binding
        self._scrollable_canvases = []

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

        # Build UI — status bar FIRST (packed at bottom) so it's always visible,
        # then notebook fills the remaining space.
        self._build_menu()
        self._build_status_bar()
        self._build_notebook()

        # Load initial data
        self.refresh_status()

        # Bind right-click copy/paste context menu to all Entry & Text widgets
        self._bind_clipboard_to_all(self.root)

    # ─────────────── Right-click copy/paste for Entry & Text ───────────────
    def _bind_clipboard_context_menu(self, widget):
        """Bind a right-click context menu (Cut/Copy/Paste/Select All) to a widget.

        Works for both ttk.Entry and tk.Text widgets.
        Call this after creating any Entry or Text widget.
        """
        menu = tk.Menu(widget, tearoff=0)

        def _cut():
            try:
                if isinstance(widget, tk.Text):
                    selected = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                    widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                else:
                    selected = widget.selection_get()
                    widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
            except tk.TclError:
                pass

        def _copy():
            try:
                if isinstance(widget, tk.Text):
                    selected = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                else:
                    selected = widget.selection_get()
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
            except tk.TclError:
                pass

        def _paste():
            try:
                clip = self.root.clipboard_get()
                if isinstance(widget, tk.Text):
                    # Try to delete selection first
                    try:
                        widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                    except tk.TclError:
                        pass
                    widget.insert(tk.INSERT, clip)
                else:
                    try:
                        widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
                    except tk.TclError:
                        pass
                    widget.insert(tk.INSERT, clip)
            except tk.TclError:
                pass

        def _select_all():
            try:
                if isinstance(widget, tk.Text):
                    widget.tag_add(tk.SEL, "1.0", tk.END)
                    widget.mark_set(tk.INSERT, tk.END)
                    widget.see(tk.END)
                else:
                    widget.select_range(0, tk.END)
                    widget.icursor(tk.END)
            except tk.TclError:
                pass

        menu.add_command(label="Cut", command=_cut)
        menu.add_command(label="Copy", command=_copy)
        menu.add_command(label="Paste", command=_paste)
        menu.add_separator()
        menu.add_command(label="Select All", command=_select_all)

        def _show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", _show_menu)

    def _bind_clipboard_to_all(self, widget):
        """Recursively bind clipboard context menu to all Entry and Text widgets."""
        for child in widget.winfo_children():
            if isinstance(child, (ttk.Entry, tk.Entry, tk.Text)):
                self._bind_clipboard_context_menu(child)
            self._bind_clipboard_to_all(child)

    # ─────────────── Scrollable canvas helper ───────────────
    def make_scrollable(self, parent, bg='#d9d9d9'):
        """Wrap a parent frame in a scrollable canvas.

        Returns the inner frame where widgets should be packed.
        The canvas + scrollbar are packed into the parent frame.

        Usage:
            inner = self.make_scrollable(self.upload_frame)
            ttk.Label(inner, text="...").pack(...)
        """
        canvas = tk.Canvas(parent, highlightthickness=0, bg=bg)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Resize inner frame to match canvas width
        def _on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scrolling (active only when cursor is over this canvas)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self._scrollable_canvases.append(canvas)
        return inner

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
        self.orphans_frame = ttk.Frame(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.upload_frame, text="  📤 Upload  ")
        self.notebook.add(self.download_frame, text="  📥 Download  ")
        self.notebook.add(self.browse_frame, text="  🗄️ Browse  ")
        self.notebook.add(self.orphans_frame, text="  👻 Orphans  ")
        self.notebook.add(self.settings_frame, text="  ⚙️ Configuration  ")

        self._build_upload_tab()
        self._build_download_tab()
        self._build_browse_tab()
        self._build_orphans_tab()
        self._build_settings_tab()

    # ─────────────── Status Bar ───────────────
    def _build_status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        # Pack at BOTTOM first so it's always visible regardless of window size
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=4)

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

        # Upload destination (multi-channel support)
        dest_frame = ttk.LabelFrame(frame, text="📤 Upload Destination", padding=12)
        dest_frame.pack(fill=tk.X, padx=16, pady=4)

        dest_row = ttk.Frame(dest_frame)
        dest_row.pack(fill=tk.X)
        ttk.Label(dest_row, text="Channel:").pack(side=tk.LEFT)
        self.upload_channel = tk.StringVar(value="main")  # default: main channel
        self.upload_channel_combo = ttk.Combobox(dest_row, textvariable=self.upload_channel,
                                                   values=["main"], width=30, state='readonly')
        self.upload_channel_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.upload_all_channels = tk.BooleanVar(value=False)
        ttk.Checkbutton(dest_row, text="Upload to ALL channels",
                        variable=self.upload_all_channels).pack(side=tk.LEFT, padx=(12, 0))

        # Manifest type selector
        mt_row = ttk.Frame(dest_frame)
        mt_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(mt_row, text="Manifest type:").pack(side=tk.LEFT)
        self.upload_manifest_type = tk.StringVar(value="default")
        ttk.Combobox(mt_row, textvariable=self.upload_manifest_type,
                     values=["default", "text", "file", "auto"],
                     width=12, state='readonly').pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(mt_row, text="default=use config setting",
                  style='Status.TLabel').pack(side=tk.LEFT)
        ttk.Label(dest_frame, text="Use the Configuration tab to add more storage channels.",
                  style='Status.TLabel').pack(anchor=tk.W, pady=(4, 0))

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

        # Multi-channel: upload to all channels or a specific one
        if self.upload_all_channels.get():
            args.append("--all-channels")
        else:
            selected_channel = self.upload_channel.get()
            if selected_channel and selected_channel != "main":
                # Find the actual channel ID from the config
                self.config = tgv.Config.load(self.config_path)
                all_chs = self.config.get_all_storage_channels()
                # The combo box shows "main (ID)" or just the ID
                # Try to extract the channel ID
                for ch in all_chs:
                    if str(ch) in selected_channel or selected_channel in str(ch):
                        args.extend(["--channel", str(ch)])
                        break

        # Manifest type override (if not "default")
        mt = self.upload_manifest_type.get()
        if mt and mt != "default":
            args.extend(["--manifest-type", mt])

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

        ttk.Label(frame, text="Download Manager", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        # ─── New Download Section ───
        link_frame = ttk.LabelFrame(frame, text="New Download", padding=12)
        link_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(link_frame, text="Manifest link(s) — one per line:").pack(anchor=tk.W)
        self.download_links = tk.Text(link_frame, height=3, wrap=tk.WORD)
        self.download_links.pack(fill=tk.X, pady=(4, 8))

        # Options row
        opt_row = ttk.Frame(link_frame)
        opt_row.pack(fill=tk.X)

        ttk.Label(opt_row, text="Output dir:").pack(side=tk.LEFT)
        self.download_outdir = tk.StringVar(value=os.path.join(_PROJECT_ROOT, "downloads"))
        ttk.Entry(opt_row, textvariable=self.download_outdir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        ttk.Button(opt_row, text="Browse...", command=self._browse_outdir).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(opt_row, text="Password:").pack(side=tk.LEFT)
        self.download_password = tk.StringVar()
        ttk.Entry(opt_row, textvariable=self.download_password, show="•", width=20).pack(side=tk.LEFT, padx=(4, 0))

        # Start button
        btn_row = ttk.Frame(link_frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_row, text="📥 Start Download", style='Accent.TButton',
                   command=self.do_download).pack(side=tk.LEFT)

        # ─── Downloads List ───
        list_frame = ttk.LabelFrame(frame, text="Downloads", padding=8)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        # Treeview for downloads
        dl_tree_frame = ttk.Frame(list_frame)
        dl_tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("id", "name", "progress", "size", "status", "speed")
        self.download_tree = ttk.Treeview(dl_tree_frame, columns=columns, show='headings',
                                           selectmode='extended', height=8)

        self.download_tree.heading("id", text="ID")
        self.download_tree.heading("name", text="File Name")
        self.download_tree.heading("progress", text="Progress")
        self.download_tree.heading("size", text="Size")
        self.download_tree.heading("status", text="Status")
        self.download_tree.heading("speed", text="Speed")

        self.download_tree.column("id", width=30, anchor='center')
        self.download_tree.column("name", width=200)
        self.download_tree.column("progress", width=80, anchor='center')
        self.download_tree.column("size", width=80, anchor='e')
        self.download_tree.column("status", width=80, anchor='center')
        self.download_tree.column("speed", width=80, anchor='e')

        dl_scrollbar = ttk.Scrollbar(dl_tree_frame, orient=tk.VERTICAL,
                                      command=self.download_tree.yview)
        self.download_tree.configure(yscrollcommand=dl_scrollbar.set)
        self.download_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dl_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Control buttons
        ctrl_frame = ttk.Frame(list_frame)
        ctrl_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(ctrl_frame, text="⏸️ Pause",
                   command=lambda: self._download_action("pause")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl_frame, text="▶️ Resume",
                   command=lambda: self._download_action("resume")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl_frame, text="❌ Cancel",
                   command=lambda: self._download_action("cancel")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl_frame, text="🗑️ Remove",
                   command=lambda: self._download_action("remove")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl_frame, text="🧹 Clear Completed",
                   command=self._clear_completed_downloads).pack(side=tk.LEFT, padx=(0, 4))

        # Right-click context menu
        self.dl_context_menu = tk.Menu(self.download_tree, tearoff=0)
        self.dl_context_menu.add_command(label="⏸️ Pause", command=lambda: self._download_action("pause"))
        self.dl_context_menu.add_command(label="▶️ Resume", command=lambda: self._download_action("resume"))
        self.dl_context_menu.add_command(label="❌ Cancel", command=lambda: self._download_action("cancel"))
        self.dl_context_menu.add_separator()
        self.dl_context_menu.add_command(label="🗑️ Remove", command=lambda: self._download_action("remove"))
        self.dl_context_menu.add_command(label="📂 Open Folder", command=self._open_download_folder)

        def on_dl_right_click(event):
            item = self.download_tree.identify_row(event.y)
            if item:
                self.download_tree.selection_set(item)
                self.dl_context_menu.tk_popup(event.x_root, event.y_root)

        self.download_tree.bind("<Button-3>", on_dl_right_click)

        # Initialize download manager
        from tg_vault.download_manager import DownloadManager

        # Set up download manager with concurrency control and persistence.
        # max_concurrent = number of bots (safe: 1 API call at a time per bot).
        # State file: downloads.json next to the config file.
        self.config = tgv.Config.load(self.config_path)
        max_concurrent = max(1, len(self.config.bots))
        state_file = os.path.join(os.path.dirname(self.config_path), "downloads.json")
        self.download_manager = DownloadManager(
            max_concurrent=max_concurrent,
            state_file=state_file,
        )

        # Load persisted downloads (they start in "paused" state)
        bot_pool = tgv.BotPool(self.config.bots)
        db = self.config.get_db()
        self.download_manager.load_state(self.config, bot_pool, db)

        # Start the UI refresh timer (updates download list every 500ms)
        self._refresh_download_ui()

    def _browse_outdir(self):
        d = filedialog.askdirectory(title="Select output directory")
        if d:
            self.download_outdir.set(d)

    def do_download(self):
        """Start one or more downloads via the DownloadManager."""
        links_text = self.download_links.get(1.0, tk.END).strip()
        if not links_text:
            messagebox.showwarning("No links", "Please enter at least one link.")
            return

        links = [l.strip() for l in links_text.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if not links:
            messagebox.showwarning("No links", "No valid links found.")
            return

        password = self.download_password.get().strip() or None
        out_dir = self.download_outdir.get().strip() or "."

        # Create output dir if it doesn't exist
        os.makedirs(out_dir, exist_ok=True)

        # Create a BotPool for the download manager
        self.config = tgv.Config.load(self.config_path)
        bot_pool = tgv.BotPool(self.config.bots)
        if len(bot_pool) == 0:
            messagebox.showerror("No bots", "No active bots configured.")
            return

        db = self.config.get_db()

        # Add each link as a separate download task
        for link in links:
            task_id = self.download_manager.add_download(
                link, out_dir, self.config, bot_pool,
                password=password, db=db
            )
            self.status_label.config(text=f"Started download #{task_id}")

        # Clear the links text box
        self.download_links.delete(1.0, tk.END)

    def _download_action(self, action):
        """Perform an action (pause/resume/cancel/remove) on selected download(s).

        Supports multiselect — applies the action to all selected downloads.
        """
        selected = self.download_tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more downloads.")
            return

        # Collect all selected task IDs
        task_ids = []
        for item in selected:
            vals = self.download_tree.item(item, "values")
            task_ids.append(int(vals[0]))

        if action == "cancel":
            if not messagebox.askyesno("Cancel",
                f"Cancel {len(task_ids)} download(s)?"):
                return

        if action == "remove":
            # Remove only completed/cancelled/failed
            removable = []
            for tid in task_ids:
                task = self.download_manager.get_task(tid)
                if task and task.state in ("completed", "cancelled", "failed"):
                    removable.append(tid)
            if not removable:
                messagebox.showwarning("Cannot remove",
                    "Can only remove completed, cancelled, or failed downloads.")
                return
            # Clear selection BEFORE removing to prevent treeview errors
            self.download_tree.selection_remove(self.download_tree.selection())
            for tid in removable:
                self.download_manager.remove(tid)
            self.status_label.config(text=f"Removed {len(removable)} download(s)")
            return

        # Apply action to all selected
        for tid in task_ids:
            if action == "pause":
                self.download_manager.pause(tid)
            elif action == "resume":
                self.download_manager.resume(tid)
            elif action == "cancel":
                self.download_manager.cancel(tid)

    def _clear_completed_downloads(self):
        """Remove all completed/cancelled/failed downloads."""
        n = self.download_manager.clear_completed()
        self.status_label.config(text=f"Cleared {n} completed download(s)")

    def _open_download_folder(self):
        """Open the output directory in the file explorer."""
        out_dir = self.download_outdir.get().strip() or "."
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(out_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", out_dir])
            else:
                subprocess.run(["xdg-open", out_dir])
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open folder: {e}")

    def _refresh_download_ui(self):
        """Refresh the download list every 500ms.

        Preserves ALL selected task IDs across refreshes.
        """
        # Remember ALL selected task IDs (not just the first)
        selected_task_ids = set()
        for item in self.download_tree.selection():
            vals = self.download_tree.item(item, "values")
            if vals:
                selected_task_ids.add(str(vals[0]))

        # Clear and re-populate the tree
        for item in self.download_tree.get_children():
            self.download_tree.delete(item)

        for task in self.download_manager.get_all_tasks():
            # Calculate progress and speed
            if task.total_parts > 0:
                progress_pct = (task.completed_parts / task.total_parts) * 100
                progress_str = f"{progress_pct:.0f}% ({task.completed_parts}/{task.total_parts})"
            else:
                progress_str = "—"

            size_str = tgv.format_size(task.file_size) if task.file_size else "—"

            # Calculate speed
            speed_str = "—"
            if task.state == "downloading" and task.started_at:
                elapsed = time.time() - task.started_at
                if elapsed > 0 and task.downloaded_bytes > 0:
                    speed = task.downloaded_bytes / elapsed
                    speed_str = tgv.format_speed(speed)

            # Status with icon
            status_icons = {
                "pending": "⏳",
                "downloading": "⬇️",
                "paused": "⏸️",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }
            status_str = f"{status_icons.get(task.state, '?')} {task.state}"

            name = task.file_name or "Fetching manifest..."
            if task.state == "failed" and task.error:
                name = f"{name} ({task.error[:30]})"

            item_id = self.download_tree.insert("", tk.END, values=(
                task.id, name, progress_str, size_str, status_str, speed_str
            ))

            # Re-select if this was one of the selected tasks
            if str(task.id) in selected_task_ids:
                self.download_tree.selection_add(item_id)

        # Periodically save download state (every 5 seconds = every 10 refreshes)
        self._dl_save_counter = getattr(self, '_dl_save_counter', 0) + 1
        if self._dl_save_counter >= 10:
            self._dl_save_counter = 0
            self.download_manager._save_state()

        # Schedule next refresh
        self.root.after(500, self._refresh_download_ui)

    def _on_download_progress(self, line):
        """Legacy: called for each line of download output — ignored now."""
        pass

    def _on_download_done(self, success, output):
        """Legacy: download completion callback — ignored now."""
        pass

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

        columns = ("id", "name", "size", "parts", "enc", "tags", "desc", "date", "link")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', selectmode='extended')

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("parts", text="Parts")
        self.tree.heading("enc", text="🔒")
        self.tree.heading("tags", text="Tags")
        self.tree.heading("desc", text="Description")
        self.tree.heading("date", text="Date")
        self.tree.heading("link", text="Link")

        self.tree.column("id", width=40, anchor='center')
        self.tree.column("name", width=150)
        self.tree.column("size", width=70, anchor='e')
        self.tree.column("parts", width=45, anchor='center')
        self.tree.column("enc", width=30, anchor='center')
        self.tree.column("tags", width=120)
        self.tree.column("desc", width=180)
        self.tree.column("date", width=130)
        self.tree.column("link", width=180)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Action buttons
        action_frame = ttk.Frame(frame)
        action_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

        ttk.Button(action_frame, text="📥 Download Selected", command=self.download_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📥 Download All Matching", command=self.download_all_matching).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="✏️ Edit Selected", command=self.edit_selected_file).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📋 Copy Link", command=self.copy_link).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🔄 Refresh", command=self.refresh_file_list).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🗑️ Delete Selected", command=self.delete_selected).pack(side=tk.LEFT, padx=(0, 4))

        self.result_count_label = ttk.Label(action_frame, text="", style='Status.TLabel')
        self.result_count_label.pack(side=tk.RIGHT)

        self.tree.bind("<Double-1>", lambda e: self.download_selected())

        # Right-click context menu
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="📥 Download", command=self.download_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="✏️ Edit Description / Tags", command=self.edit_selected_file)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📋 Copy Link", command=self.copy_link)
        self.context_menu.add_command(label="📋 Copy Name", command=self.copy_name)
        self.context_menu.add_command(label="📋 Copy ID", command=self.copy_id)
        self.context_menu.add_command(label="📋 Copy All Links (Selected)", command=self.copy_all_links)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ Delete from Telegram + DB", command=self.delete_selected)

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

        # ─── Edit Panel (hidden by default, shown when editing) ───
        self.edit_panel = ttk.LabelFrame(frame, text="✏️ Edit File Metadata", padding=12)
        # Not packed initially — shown only when user clicks Edit

        self.edit_file_id = tk.StringVar()
        self.edit_file_name = tk.StringVar()
        self.edit_desc = tk.StringVar()
        self.edit_tags = tk.StringVar()
        self.edit_mode = tk.StringVar(value="single")  # "single" or "bulk"

        # Row: file info
        info_row = ttk.Frame(self.edit_panel)
        info_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(info_row, text="File:", style='Title.TLabel').pack(side=tk.LEFT)
        ttk.Label(info_row, textvariable=self.edit_file_name, style='Title.TLabel').pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(info_row, text="ID:").pack(side=tk.LEFT)
        ttk.Label(info_row, textvariable=self.edit_file_id).pack(side=tk.LEFT, padx=(4, 0))

        # Description
        desc_row = ttk.Frame(self.edit_panel)
        desc_row.pack(fill=tk.X, pady=2)
        ttk.Label(desc_row, text="Description:").pack(side=tk.LEFT)
        ttk.Entry(desc_row, textvariable=self.edit_desc).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        # Tags
        tag_row = ttk.Frame(self.edit_panel)
        tag_row.pack(fill=tk.X, pady=2)
        ttk.Label(tag_row, text="Tags:").pack(side=tk.LEFT)
        ttk.Entry(tag_row, textvariable=self.edit_tags).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        ttk.Label(tag_row, text="(comma-separated)", style='Status.TLabel').pack(side=tk.LEFT, padx=(4, 0))

        # Buttons
        edit_btn_row = ttk.Frame(self.edit_panel)
        edit_btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(edit_btn_row, text="💾 Update Telegram + DB", style='Accent.TButton',
                   command=self.apply_edit).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(edit_btn_row, text="Cancel", command=self.hide_edit_panel).pack(side=tk.LEFT, padx=(0, 4))

    def edit_selected_file(self):
        """Show the edit panel for the selected file(s).

        Supports single-file edit (shows current values) and bulk edit
        (fields start empty, applied to all selected files).
        """
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select a file to edit.")
            return

        if len(selected) == 1:
            # Single file — show current values
            vals = self.tree.item(selected[0], "values")
            # columns: (id, name, size, parts, enc, tags, desc, date, link)
            file_id = vals[0]
            file_name = vals[1]
            tags_str = vals[5] if len(vals) > 5 else ""
            desc_str = vals[6] if len(vals) > 6 else ""

            self.edit_file_id.set(file_id)
            self.edit_file_name.set(file_name)
            self.edit_desc.set(desc_str)
            self.edit_tags.set(tags_str)
            self.edit_mode.set("single")
            self.edit_panel.config(text=f"✏️ Edit File #{file_id}")
        else:
            # Bulk edit — fields start empty
            ids = []
            names = []
            for item in selected:
                vals = self.tree.item(item, "values")
                ids.append(vals[0])
                names.append(vals[1])
            self.edit_file_id.set(",".join(ids))
            self.edit_file_name.set(f"{len(ids)} files selected")
            self.edit_desc.set("")
            self.edit_tags.set("")
            self.edit_mode.set("bulk")
            self.edit_panel.config(text=f"✏️ Bulk Edit ({len(ids)} files)")

        # Show the edit panel
        self.edit_panel.pack(fill=tk.X, padx=16, pady=(0, 8))
        self.status_label.config(text=f"Editing {len(selected)} file(s)")

    def hide_edit_panel(self):
        """Hide the edit panel."""
        self.edit_panel.pack_forget()

    def apply_edit(self):
        """Apply the edit by calling db edit.

        In single mode: db edit <ID> --desc ... --tag ...
        In bulk mode:   db edit --ids 1,2,3 --desc ... --tag ...
                        (or --add-tag / --remove-tag)
        """
        ids_str = self.edit_file_id.get()
        if not ids_str:
            return

        new_desc = self.edit_desc.get()
        new_tags = self.edit_tags.get()
        is_bulk = self.edit_mode.get() == "bulk"

        if is_bulk:
            args = ["db", "edit", "--ids", ids_str]
            if new_desc:
                args.extend(["--desc", new_desc])
            if new_tags:
                # In bulk mode, --tag replaces all tags.
                # If user wants to add tags without replacing, they should
                # use the add-tag field. But for simplicity, we use --tag here.
                args.extend(["--tag", new_tags])
            label = f"{ids_str.count(',') + 1} files"
        else:
            args = ["db", "edit", ids_str]
            if new_desc is not None:
                args.extend(["--desc", new_desc])
            if new_tags:
                args.extend(["--tag", new_tags])
            label = f"file #{ids_str}"

        def done_cb(success, output):
            if success:
                self.status_label.config(text=f"✅ Updated {label}")
                self.hide_edit_panel()
                self.refresh_file_list()
            else:
                messagebox.showerror("Edit Failed", output[-800:])

        self._run_async(f"Editing {label}...", args, callback=done_cb)

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
            # Parse tags and description
            try:
                import json as _json
                tags_list = _json.loads(r.get("hashtags", "[]")) if r.get("hashtags") else []
            except Exception:
                tags_list = []
            tags_str = ", ".join(tags_list)
            desc_str = r.get("description") or ""
            self.tree.insert("", tk.END,
                values=(r["id"], r["name"], size_str, r["total_parts"], enc_str,
                        tags_str, desc_str, date_str, link))

        self.result_count_label.config(text=f"{len(rows)} of {count} files")

    def download_selected(self):
        """Download selected files from the Browse tab via DownloadManager."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more files.")
            return

        # Get file IDs and look up their share links from DB
        db = self.config.get_db()
        if db is None:
            messagebox.showwarning("Database not enabled", "Please enable the database first.")
            return

        links = []
        for item in selected:
            vals = self.tree.item(item, "values")
            file_id = int(vals[0])
            record = db.get_file_by_id(file_id)
            if record and record.get("share_link"):
                links.append(record["share_link"])
            else:
                messagebox.showwarning("No link", f"File #{file_id} has no share link in DB.")
                return

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        os.makedirs(out_dir, exist_ok=True)

        # Create a BotPool for the download manager
        self.config = tgv.Config.load(self.config_path)
        bot_pool = tgv.BotPool(self.config.bots)
        if len(bot_pool) == 0:
            messagebox.showerror("No bots", "No active bots configured.")
            return

        password = self.download_password.get().strip() if hasattr(self, 'download_password') else None

        for link in links:
            self.download_manager.add_download(
                link, out_dir, self.config, bot_pool,
                password=password or None, db=db
            )

        # Switch to Download tab
        self.notebook.select(1)
        self.status_label.config(text=f"Started {len(links)} download(s)")

    def download_all_matching(self):
        """Download all files matching the current filter via DownloadManager."""
        db = self.config.get_db()
        if db is None:
            messagebox.showwarning("Database not enabled", "Please enable the database first.")
            return

        # Build filters from the Browse tab
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

        # Remove sort/limit/offset for download — we want ALL matches
        rows = db.query_files(filters)

        if not rows:
            messagebox.showinfo("No files", "No files match the current filter.")
            return

        links = [r["share_link"] for r in rows if r.get("share_link")]
        if not links:
            messagebox.showwarning("No links", "None of the matching files have share links.")
            return

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        os.makedirs(out_dir, exist_ok=True)

        self.config = tgv.Config.load(self.config_path)
        bot_pool = tgv.BotPool(self.config.bots)
        if len(bot_pool) == 0:
            messagebox.showerror("No bots", "No active bots configured.")
            return

        password = self.download_password.get().strip() if hasattr(self, 'download_password') else None

        for link in links:
            self.download_manager.add_download(
                link, out_dir, self.config, bot_pool,
                password=password or None, db=db
            )

        self.notebook.select(1)
        self.status_label.config(text=f"Started {len(links)} download(s)")

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
        # columns: (id, name, size, parts, enc, tags, desc, date, link)
        # so link is at index 8
        link = vals[8] if len(vals) > 8 else ""
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
            # columns: (id, name, size, parts, enc, tags, desc, date, link)
            link = vals[8] if len(vals) > 8 else ""
            if link:
                links.append(link)
        if links:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(links))
            self.status_label.config(text=f"Copied {len(links)} link(s)")

    def find_orphans_old(self):
        """Deprecated: orphan scanning now lives in the Orphans tab."""
        pass

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
    # ORPHANS TAB
    # ════════════════════════════════════════════════════════════
    def _build_orphans_tab(self):
        frame = self.orphans_frame

        ttk.Label(frame, text="Orphaned Files", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))
        ttk.Label(frame,
            text="Orphans are messages in your channel that have a file (document) or are a manifest,\n"
                 "but are NOT tracked in your database. Each orphan = one individual message.\n"
                 "Select individual messages and delete them — each is checked independently.",
            style='Status.TLabel', justify=tk.LEFT
        ).pack(anchor=tk.W, padx=16, pady=(0, 8))

        # Scan controls
        scan_frame = ttk.LabelFrame(frame, text="🔍 Scan Channel for Orphans", padding=12)
        scan_frame.pack(fill=tk.X, padx=16, pady=4)

        row1 = ttk.Frame(scan_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Max messages to scan:").pack(side=tk.LEFT)
        self.orphan_max_scan = tk.IntVar(value=500)
        ttk.Entry(row1, textvariable=self.orphan_max_scan, width=10).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(row1, text="Batch size:").pack(side=tk.LEFT)
        self.orphan_batch_size = tk.IntVar(value=500)
        ttk.Entry(row1, textvariable=self.orphan_batch_size, width=10).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(row1, text="Delay (s):").pack(side=tk.LEFT)
        self.orphan_delay = tk.DoubleVar(value=0.5)
        ttk.Entry(row1, textvariable=self.orphan_delay, width=6).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Button(row1, text="🔍 Scan Now", style='Accent.TButton', command=self.scan_orphans).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(scan_frame,
            text="Tip: Default 500 messages is fast (~1 min). For deep scans set 5000-10000.\n"
                 "Scan is incremental — already-known messages are skipped without API calls.",
            style='Status.TLabel', justify=tk.LEFT
        ).pack(anchor=tk.W, pady=(4, 0))

        # Orphan list (Treeview)
        list_frame = ttk.LabelFrame(frame, text="📋 Found Orphans", padding=8)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("oid", "msg_id", "type", "name", "size", "discovered", "link")
        self.orphan_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', selectmode='extended')

        self.orphan_tree.heading("oid", text="ID")
        self.orphan_tree.heading("msg_id", text="Msg ID")
        self.orphan_tree.heading("type", text="Type")
        self.orphan_tree.heading("name", text="Name / Preview")
        self.orphan_tree.heading("size", text="Size")
        self.orphan_tree.heading("discovered", text="Discovered")
        self.orphan_tree.heading("link", text="Link")

        self.orphan_tree.column("oid", width=40, anchor='center')
        self.orphan_tree.column("msg_id", width=70, anchor='center')
        self.orphan_tree.column("type", width=80, anchor='center')
        self.orphan_tree.column("name", width=220)
        self.orphan_tree.column("size", width=80, anchor='e')
        self.orphan_tree.column("discovered", width=140)
        self.orphan_tree.column("link", width=180)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.orphan_tree.yview)
        self.orphan_tree.configure(yscrollcommand=scrollbar.set)
        self.orphan_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Action buttons
        action_frame = ttk.Frame(frame)
        action_frame.pack(fill=tk.X, padx=16, pady=(4, 8))

        ttk.Button(action_frame, text="🗑️ Delete Selected", command=self.delete_orphans_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🗑️ Delete ALL", command=self.delete_orphans_all).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📋 Copy Link", command=self.copy_orphan_link).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="📥 Download Selected", command=self.download_orphans_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🧹 Clear Local List", command=self.clear_orphans_local).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="🔄 Refresh", command=self.refresh_orphan_list).pack(side=tk.LEFT, padx=(0, 4))

        self.orphan_count_label = ttk.Label(action_frame, text="", style='Status.TLabel')
        self.orphan_count_label.pack(side=tk.RIGHT)

        # Right-click context menu for orphans
        self.orphan_context_menu = tk.Menu(self.orphan_tree, tearoff=0)
        self.orphan_context_menu.add_command(label="🗑️ Delete from Telegram + DB", command=self.delete_orphans_selected)
        self.orphan_context_menu.add_separator()
        self.orphan_context_menu.add_command(label="📋 Copy Link", command=self.copy_orphan_link)
        self.orphan_context_menu.add_command(label="📥 Download", command=self.download_orphans_selected)

        def on_orphan_right_click(event):
            item = self.orphan_tree.identify_row(event.y)
            if item:
                if item not in self.orphan_tree.selection():
                    self.orphan_tree.selection_set(item)
                self.orphan_context_menu.tk_popup(event.x_root, event.y_root)

        self.orphan_tree.bind("<Button-3>", on_orphan_right_click)
        self.orphan_tree.bind("<Delete>", lambda e: self.delete_orphans_selected())

        # Output / progress box
        ttk.Label(frame, text="Output:", style='Title.TLabel').pack(anchor=tk.W, padx=16, pady=(8, 4))
        self.orphan_output = tk.Text(frame, height=6, state='disabled', wrap=tk.WORD)
        self.orphan_output.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # Load existing orphans from DB
        self.refresh_orphan_list()

    def refresh_orphan_list(self):
        """Reload orphans from the local DB into the treeview."""
        for item in self.orphan_tree.get_children():
            self.orphan_tree.delete(item)

        db = self.config.get_db()
        if db is None:
            self.orphan_count_label.config(text="DB not enabled")
            return

        try:
            orphans = db.list_orphans(include_deleted=False)
        except Exception as e:
            self.orphan_count_label.config(text=f"Error: {e}")
            return

        for o in orphans:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(o["discovered_at"]))
            name = o.get("name") or "?"
            link = o.get("share_link") or ""
            msg_type = o.get("message_type") or "?"
            size_val = o.get("file_size")
            size_str = tgv.format_size(size_val) if size_val else "—"
            self.orphan_tree.insert(
                "", tk.END,
                values=(o["id"], o["msg_id"], msg_type, name, size_str, when, link)
            )

        self.orphan_count_label.config(text=f"{len(orphans)} orphan(s) in local DB")

    def scan_orphans(self):
        """Trigger a channel scan for orphaned manifests."""
        # Validate inputs
        try:
            max_scan = int(self.orphan_max_scan.get())
            batch_size = int(self.orphan_batch_size.get())
            delay = float(self.orphan_delay.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid input", "Max scan, batch size, and delay must be numbers.")
            return

        if max_scan < 1 or batch_size < 1:
            messagebox.showerror("Invalid input", "Max scan and batch size must be >= 1.")
            return

        if not messagebox.askyesno(
            "Scan for Orphans",
            f"Scan the main channel for orphaned manifests?\n\n"
            f"Max messages: {max_scan}\n"
            f"Batch size: {batch_size}\n"
            f"Delay between batches: {delay}s\n\n"
            f"This may take a while. Continue?"
        ):
            return

        # Clear output and switch to orphans tab (already there)
        self.orphan_output.config(state='normal')
        self.orphan_output.delete(1.0, tk.END)
        self.orphan_output.config(state='disabled')

        args = ["db", "find-orphans",
                "--max-scan", str(max_scan),
                "--batch-size", str(batch_size),
                "--delay", str(delay)]

        def progress_cb(line):
            self.orphan_output.config(state='normal')
            self.orphan_output.insert(tk.END, line + "\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')

        def done_cb(success, output):
            self.orphan_output.config(state='normal')
            if success:
                self.orphan_output.insert(tk.END, "\n✅ Scan complete!\n")
            else:
                self.orphan_output.insert(tk.END, f"\n❌ FAILED\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')
            self.refresh_orphan_list()
            if not success:
                messagebox.showerror("Scan Failed", output[-800:])

        self._run_async("Scanning for orphans...", args,
                        callback=done_cb, progress_callback=progress_cb)

    def delete_orphans_selected(self):
        """Delete selected orphans from both Telegram and the local DB."""
        selected = self.orphan_tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more orphans to delete.")
            return

        ids = []
        names = []
        for item in selected:
            vals = self.orphan_tree.item(item, "values")
            # columns: (oid, msg_id, type, name, size, discovered, link)
            ids.append(vals[0])
            names.append(vals[3] if len(vals) > 3 else "?")

        msg = f"Delete {len(ids)} orphan(s) from Telegram AND local DB?\n\n"
        for n in names[:5]:
            msg += f"  • {n}\n"
        if len(names) > 5:
            msg += f"  ... and {len(names) - 5} more\n"
        msg += "\nThis cannot be undone!"

        if not messagebox.askyesno("Confirm Delete", msg, icon='warning'):
            return

        # Clear output box
        self.orphan_output.config(state='normal')
        self.orphan_output.delete(1.0, tk.END)
        self.orphan_output.insert(tk.END, f"🗑️ Deleting {len(ids)} orphan(s)...\n")
        self.orphan_output.config(state='disabled')

        args = ["db", "orphans", "delete", "--ids", ",".join(ids), "--force"]

        def progress_cb(line):
            self.orphan_output.config(state='normal')
            self.orphan_output.insert(tk.END, line + "\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')

        def done_cb(success, output):
            self.orphan_output.config(state='normal')
            if success:
                self.orphan_output.insert(tk.END, "\n✅ Done!\n")
            else:
                self.orphan_output.insert(tk.END, f"\n❌ FAILED\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')
            self.refresh_orphan_list()
            if not success:
                messagebox.showerror("Delete Failed", output[-800:])

        self._run_async(f"Deleting {len(ids)} orphan(s)...", args,
                        callback=done_cb, progress_callback=progress_cb)

    def delete_orphans_all(self):
        """Delete ALL orphans from Telegram + DB."""
        # Count first
        db = self.config.get_db()
        if db is None:
            messagebox.showwarning("Database not enabled", "Please enable the database first.")
            return
        try:
            orphans = db.list_orphans(include_deleted=False)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        if not orphans:
            messagebox.showinfo("Nothing to delete", "No orphans in the local DB.")
            return

        msg = (f"Delete ALL {len(orphans)} orphan(s) from Telegram AND local DB?\n\n"
               f"This will delete every manifest + its part messages from Telegram.\n\n"
               f"This CANNOT be undone!")
        if not messagebox.askyesno("Delete ALL Orphans", msg, icon='warning'):
            return

        self.orphan_output.config(state='normal')
        self.orphan_output.delete(1.0, tk.END)
        self.orphan_output.insert(tk.END, f"🗑️ Deleting ALL {len(orphans)} orphan(s)...\n")
        self.orphan_output.config(state='disabled')

        args = ["db", "orphans", "delete", "--ids", "all", "--force"]

        def progress_cb(line):
            self.orphan_output.config(state='normal')
            self.orphan_output.insert(tk.END, line + "\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')

        def done_cb(success, output):
            self.orphan_output.config(state='normal')
            if success:
                self.orphan_output.insert(tk.END, "\n✅ Done!\n")
            else:
                self.orphan_output.insert(tk.END, f"\n❌ FAILED\n")
            self.orphan_output.see(tk.END)
            self.orphan_output.config(state='disabled')
            self.refresh_orphan_list()
            if not success:
                messagebox.showerror("Delete Failed", output[-800:])

        self._run_async(f"Deleting ALL orphans...", args,
                        callback=done_cb, progress_callback=progress_cb)

    def copy_orphan_link(self):
        selected = self.orphan_tree.selection()
        if not selected:
            return
        vals = self.orphan_tree.item(selected[0], "values")
        # columns: (oid, msg_id, type, name, size, discovered, link)
        # so link is at index 6
        link = vals[6] if len(vals) > 6 else ""
        if link and link != "—":
            self.root.clipboard_clear()
            self.root.clipboard_append(link)
            self.status_label.config(text=f"Copied: {link}")
        else:
            messagebox.showwarning("No link", "This orphan has no share link.")

    def download_orphans_selected(self):
        """Download the selected orphan files (manifests) — useful for re-importing."""
        selected = self.orphan_tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Please select one or more orphans.")
            return

        links = []
        for item in selected:
            vals = self.orphan_tree.item(item, "values")
            # columns: (oid, msg_id, type, name, size, discovered, link)
            link = vals[6] if len(vals) > 6 else ""
            if link and link != "—":
                links.append(link)

        if not links:
            messagebox.showwarning("No links", "Selected orphans have no share link.")
            return

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        # Switch to Download tab and show progress
        self.notebook.select(1)
        self.download_result.config(state='normal')
        self.download_result.delete(1.0, tk.END)
        self.download_result.insert(tk.END, f"📥 Downloading {len(links)} orphan file(s)...\n")
        self.download_result.config(state='disabled')

        args = ["download"] + links + ["--output-dir", out_dir]

        self._run_async(f"Downloading {len(links)} orphan file(s)...", args,
                        callback=self._on_download_done,
                        progress_callback=self._on_download_progress)

    def clear_orphans_local(self):
        """Just clear the local orphan rows (don't touch Telegram)."""
        db = self.config.get_db()
        if db is None:
            messagebox.showwarning("Database not enabled", "Please enable the database first.")
            return

        if not messagebox.askyesno(
            "Clear Local Orphan List",
            "Clear ALL orphan rows from the local database?\n\n"
            "Messages in Telegram will NOT be deleted.\n"
            "Use this if you want to start fresh — re-scan to repopulate."
        ):
            return

        try:
            n = db.clear_orphans(include_deleted=False)
            self.refresh_orphan_list()
            messagebox.showinfo("Cleared", f"Cleared {n} orphan row(s) from the local DB.")
            self.status_label.config(text=f"Cleared {n} orphan row(s)")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ════════════════════════════════════════════════════════════
    # SETTINGS TAB (Configuration editor)
    # ════════════════════════════════════════════════════════════
    def _build_settings_tab(self):
        # Use the shared scrollable helper (consistent with other tabs)
        scroll_frame = self.make_scrollable(self.settings_frame)

        ttk.Label(scroll_frame, text="Configuration", style='Header.TLabel').pack(anchor=tk.W, padx=16, pady=(16, 8))

        # ─── Bots Section ───
        bots_frame = ttk.LabelFrame(scroll_frame, text="🤖 Bots (round-robin pool)", padding=12)
        bots_frame.pack(fill=tk.X, padx=16, pady=4)

        # Bot list (Treeview)
        bot_tree_frame = ttk.Frame(bots_frame)
        bot_tree_frame.pack(fill=tk.X, pady=(0, 8))

        self.bot_tree = ttk.Treeview(bot_tree_frame, columns=("idx", "username", "token_preview"),
                                      show='headings', height=4, selectmode='browse')
        self.bot_tree.heading("idx", text="#")
        self.bot_tree.heading("username", text="Username")
        self.bot_tree.heading("token_preview", text="Token (preview)")
        self.bot_tree.column("idx", width=30, anchor='center')
        self.bot_tree.column("username", width=150)
        self.bot_tree.column("token_preview", width=300)
        self.bot_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)

        bot_btns = ttk.Frame(bots_frame)
        bot_btns.pack(fill=tk.X)
        ttk.Button(bot_btns, text="➕ Add Bot", command=self.add_bot).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bot_btns, text="➖ Remove Selected Bot", command=self.remove_bot).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bot_btns, text="🔄 Refresh", command=self.refresh_bot_list).pack(side=tk.LEFT, padx=(0, 4))

        # Add-bot entry row
        add_row = ttk.Frame(bots_frame)
        add_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(add_row, text="Token:").pack(side=tk.LEFT)
        self.new_token = tk.StringVar()
        ttk.Entry(add_row, textvariable=self.new_token, show="•").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        # ─── Channels Section ───
        ch_frame = ttk.LabelFrame(scroll_frame, text="📡 Channels", padding=12)
        ch_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(ch_frame, text="Main channel:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.cfg_main_channel = tk.StringVar()
        ttk.Entry(ch_frame, textvariable=self.cfg_main_channel, width=40).grid(row=0, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        ttk.Label(ch_frame, text="(e.g. -1001234567890 or @username)", style='Status.TLabel').grid(row=0, column=2, padx=(8, 0))

        ttk.Label(ch_frame, text="Temp channel:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.cfg_temp_channel = tk.StringVar()
        ttk.Entry(ch_frame, textvariable=self.cfg_temp_channel, width=40).grid(row=1, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        ttk.Label(ch_frame, text="(optional — defaults to main)", style='Status.TLabel').grid(row=1, column=2, padx=(8, 0))

        ttk.Label(ch_frame, text="DB sync channel:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.cfg_db_sync_channel = tk.StringVar()
        ttk.Entry(ch_frame, textvariable=self.cfg_db_sync_channel, width=40).grid(row=2, column=1, sticky=tk.EW, pady=2, padx=(8, 0))
        ttk.Label(ch_frame, text="(optional — defaults to temp)", style='Status.TLabel').grid(row=2, column=2, padx=(8, 0))

        ch_frame.columnconfigure(1, weight=1)

        # ─── Storage Channels List (multi-channel support) ───
        storage_frame = ttk.LabelFrame(scroll_frame,
            text="📦 Storage Channels (file uploads + orphan scans)", padding=12)
        storage_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(storage_frame,
            text="The main channel is always included. Add more channels to upload\n"
                 "to multiple destinations, or scan all channels for orphans.",
            style='Status.TLabel', justify=tk.LEFT
        ).pack(anchor=tk.W, pady=(0, 8))

        # Storage channel treeview
        st_tree_frame = ttk.Frame(storage_frame)
        st_tree_frame.pack(fill=tk.X, pady=(0, 8))
        self.storage_tree = ttk.Treeview(st_tree_frame, columns=("idx", "channel_id", "role"),
                                          show='headings', height=4, selectmode='browse')
        self.storage_tree.heading("idx", text="#")
        self.storage_tree.heading("channel_id", text="Channel ID")
        self.storage_tree.heading("role", text="Role")
        self.storage_tree.column("idx", width=30, anchor='center')
        self.storage_tree.column("channel_id", width=250)
        self.storage_tree.column("role", width=100, anchor='center')
        self.storage_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)

        st_btns = ttk.Frame(storage_frame)
        st_btns.pack(fill=tk.X)

        add_ch_row = ttk.Frame(st_btns)
        add_ch_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(add_ch_row, text="Channel ID:").pack(side=tk.LEFT)
        self.new_storage_channel = tk.StringVar()
        ttk.Entry(add_ch_row, textvariable=self.new_storage_channel, width=30).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(add_ch_row, text="➕ Add", command=self.add_storage_channel).pack(side=tk.LEFT)

        st_row2 = ttk.Frame(st_btns)
        st_row2.pack(fill=tk.X)
        ttk.Button(st_row2, text="➖ Remove Selected", command=self.remove_storage_channel).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(st_row2, text="🔄 Refresh", command=self.refresh_storage_channels).pack(side=tk.LEFT, padx=(0, 4))

        # ─── Advanced Settings ───
        adv_frame = ttk.LabelFrame(scroll_frame, text="⚙️ Advanced Settings", padding=12)
        adv_frame.pack(fill=tk.X, padx=16, pady=4)

        ttk.Label(adv_frame, text="Chunk size (MB):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.cfg_chunk_mb = tk.IntVar()
        ttk.Entry(adv_frame, textvariable=self.cfg_chunk_mb, width=10).grid(row=0, column=1, sticky=tk.W, pady=2, padx=(8, 16))
        ttk.Label(adv_frame, text="(must be ≤ 19 for cloud Bot API)", style='Status.TLabel').grid(row=0, column=2, sticky=tk.W)

        ttk.Label(adv_frame, text="Upload delay (s):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.cfg_upload_delay = tk.DoubleVar()
        ttk.Entry(adv_frame, textvariable=self.cfg_upload_delay, width=10).grid(row=1, column=1, sticky=tk.W, pady=2, padx=(8, 16))

        ttk.Label(adv_frame, text="Download delay (s):").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.cfg_download_delay = tk.DoubleVar()
        ttk.Entry(adv_frame, textvariable=self.cfg_download_delay, width=10).grid(row=2, column=1, sticky=tk.W, pady=2, padx=(8, 16))

        ttk.Label(adv_frame, text="Parallel workers:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.cfg_parallel_workers = tk.IntVar()
        ttk.Entry(adv_frame, textvariable=self.cfg_parallel_workers, width=10).grid(row=3, column=1, sticky=tk.W, pady=2, padx=(8, 16))
        ttk.Label(adv_frame, text="(download concurrency)", style='Status.TLabel').grid(row=3, column=2, sticky=tk.W)

        ttk.Label(adv_frame, text="Default manifest:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.cfg_default_manifest_type = tk.StringVar(value="text")
        ttk.Combobox(adv_frame, textvariable=self.cfg_default_manifest_type,
                     values=["text", "file", "auto"], width=10, state='readonly').grid(
            row=4, column=1, sticky=tk.W, pady=2, padx=(8, 16))
        ttk.Label(adv_frame, text="text=editable, file=not editable, auto=text if fits",
                  style='Status.TLabel').grid(row=4, column=2, sticky=tk.W)

        # ─── Database Settings ───
        db_frame = ttk.LabelFrame(scroll_frame, text="🗄️ Database", padding=12)
        db_frame.pack(fill=tk.X, padx=16, pady=4)

        self.cfg_db_enabled = tk.BooleanVar()
        ttk.Checkbutton(db_frame, text="Enable SQLite database",
                        variable=self.cfg_db_enabled).pack(anchor=tk.W)

        self.cfg_db_auto_sync = tk.BooleanVar()
        ttk.Checkbutton(db_frame, text="Auto-sync DB to Telegram after every change",
                        variable=self.cfg_db_auto_sync).pack(anchor=tk.W)

        # DB path row — use a separate Frame so we can use pack() consistently
        dbpath_row = ttk.Frame(db_frame)
        dbpath_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(dbpath_row, text="DB path:").pack(side=tk.LEFT)
        self.cfg_db_path = tk.StringVar()
        ttk.Entry(dbpath_row, textvariable=self.cfg_db_path).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )

        # ─── Network / Proxy Section ───
        proxy_frame = ttk.LabelFrame(scroll_frame, text="🌐 Network / Proxy", padding=12)
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

        # ─── Save / Reload Buttons ───
        save_frame = ttk.Frame(scroll_frame)
        save_frame.pack(fill=tk.X, padx=16, pady=8)
        ttk.Button(save_frame, text="💾 Save Configuration", style='Accent.TButton',
                   command=self.save_configuration).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(save_frame, text="🔄 Reload from File", command=self.load_configuration).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(save_frame, text="🔌 Test Connection", command=self.test_connection).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(save_frame, text="🔧 Run Setup Wizard", command=self.run_setup).pack(side=tk.LEFT, padx=(0, 4))

        # ─── Maintenance Actions ───
        maint_frame = ttk.LabelFrame(scroll_frame, text="🛠️ Maintenance", padding=12)
        maint_frame.pack(fill=tk.X, padx=16, pady=4)

        row = ttk.Frame(maint_frame)
        row.pack(fill=tk.X, pady=2)
        ttk.Button(row, text="☁️ Sync DB to Telegram", command=self.action_db_sync).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="📥 Restore DB from Telegram", command=self.action_db_restore).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="🧹 Vacuum DB", command=self.action_db_vacuum).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="💾 Export JSON", command=self.action_db_export).pack(side=tk.LEFT, padx=(0, 4))

        row2 = ttk.Frame(maint_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Button(row2, text="🧹 Cleanup Temp Channel", command=self.action_cleanup).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="🧹 Clear Temp (keep DB)", command=self.action_clear_temp_keep_db).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="🔍 Find DB Backup", command=self.action_db_find).pack(side=tk.LEFT, padx=(0, 4))

        row3 = ttk.Frame(maint_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Button(row3, text="🔍 Verify DB Integrity", command=self.action_db_verify).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row3, text="🔍 Find Missing Files", command=self.action_find_missing).pack(side=tk.LEFT, padx=(0, 4))

        # ─── Raw Config View ───
        raw_frame = ttk.LabelFrame(scroll_frame, text="Raw Config File (read-only)", padding=12)
        raw_frame.pack(fill=tk.X, padx=16, pady=4)

        self.config_text = tk.Text(raw_frame, height=10, wrap=tk.WORD)
        self.config_text.pack(fill=tk.X)
        ttk.Button(raw_frame, text="Reload View", command=self._show_config).pack(anchor=tk.W, pady=(4, 0))

        # ─── Output ───
        ttk.Label(scroll_frame, text="Output:", style='Title.TLabel').pack(anchor=tk.W, padx=16, pady=(8, 4))
        self.settings_output = tk.Text(scroll_frame, height=6, state='disabled', wrap=tk.WORD)
        self.settings_output.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # Load current config into the form fields
        self.load_configuration()
        self._show_config()
        self.refresh_bot_list()

    def load_configuration(self):
        """Reload config from disk and populate all form fields."""
        self.config = tgv.Config.load(self.config_path)
        self.cfg_main_channel.set(str(self.config.main_channel or ""))
        self.cfg_temp_channel.set(str(self.config.temp_channel or ""))
        self.cfg_db_sync_channel.set(str(self.config.db_sync_channel or ""))
        self.cfg_chunk_mb.set(self.config.chunk_size // (1024 * 1024))
        self.cfg_upload_delay.set(self.config.upload_delay)
        self.cfg_download_delay.set(self.config.download_delay)
        self.cfg_parallel_workers.set(self.config.parallel_workers)
        self.cfg_default_manifest_type.set(getattr(self.config, 'default_manifest_type', 'text'))
        self.cfg_db_enabled.set(self.config.db_enabled)
        self.cfg_db_auto_sync.set(self.config.db_auto_sync)
        self.cfg_db_path.set(self.config.db_path or "")
        self._show_config()
        self.refresh_bot_list()
        self.refresh_storage_channels()
        self.refresh_status()

    def save_configuration(self):
        """Save all form fields back to the config file."""
        # Validate inputs
        try:
            chunk_mb = int(self.cfg_chunk_mb.get())
            if chunk_mb < 1 or chunk_mb > 19:
                messagebox.showerror("Invalid value", "Chunk size must be between 1 and 19 MB.")
                return
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid value", "Chunk size must be a number.")
            return

        try:
            upload_delay = float(self.cfg_upload_delay.get())
            download_delay = float(self.cfg_download_delay.get())
            parallel_workers = int(self.cfg_parallel_workers.get())
            if parallel_workers < 1:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid value", "Delays must be numbers, parallel_workers must be a positive integer.")
            return

        main_ch = self.cfg_main_channel.get().strip()
        if not main_ch:
            messagebox.showerror("Missing channel", "Main channel is required.")
            return

        temp_ch = self.cfg_temp_channel.get().strip() or main_ch
        db_sync_ch = self.cfg_db_sync_channel.get().strip() or None

        # Try to convert numeric channel IDs
        def _maybe_int(s):
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                return s  # keep as string (e.g. @username)

        self.config.main_channel = _maybe_int(main_ch)
        self.config.temp_channel = _maybe_int(temp_ch)
        self.config.db_sync_channel = _maybe_int(db_sync_ch)
        self.config.chunk_size = chunk_mb * 1024 * 1024
        self.config.upload_delay = upload_delay
        self.config.download_delay = download_delay
        self.config.parallel_workers = parallel_workers
        self.config.default_manifest_type = self.cfg_default_manifest_type.get()
        self.config.db_enabled = self.cfg_db_enabled.get()
        self.config.db_auto_sync = self.cfg_db_auto_sync.get()
        db_path = self.cfg_db_path.get().strip()
        self.config.db_path = db_path if db_path else None

        try:
            self.config.save()
            messagebox.showinfo("Saved", f"Configuration saved to:\n{self.config_path}")
            self._show_config()
            self.refresh_status()
            self.status_label.config(text="✅ Configuration saved")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def refresh_bot_list(self):
        """Refresh the bot list in the settings tab."""
        if not hasattr(self, 'bot_tree'):
            return
        self.config = tgv.Config.load(self.config_path)
        for item in self.bot_tree.get_children():
            self.bot_tree.delete(item)
        for i, b in enumerate(self.config.bots, 1):
            username = b.get("username", "?")
            token = b.get("token", "")
            preview = token[:15] + "..." if len(token) > 15 else token
            self.bot_tree.insert("", tk.END, values=(i, f"@{username}" if not username.startswith("@") else username, preview))

    def remove_bot(self):
        selected = self.bot_tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Select a bot to remove.")
            return
        vals = self.bot_tree.item(selected[0], "values")
        idx = int(vals[0]) - 1  # 1-based to 0-based

        if not messagebox.askyesno("Remove Bot",
            f"Remove bot @{vals[1]} (#{vals[0]})?\n\nConfig will be saved."):
            return

        self.config = tgv.Config.load(self.config_path)
        if 0 <= idx < len(self.config.bots):
            self.config.bots.pop(idx)
            self.config.save()
            self.refresh_bot_list()
            self.refresh_status()
            self.status_label.config(text=f"Removed bot #{vals[0]}")
        else:
            messagebox.showerror("Error", "Invalid bot index.")

    # ─── Storage channel management ───

    def refresh_storage_channels(self):
        """Refresh the storage channels treeview."""
        if not hasattr(self, 'storage_tree'):
            return
        self.config = tgv.Config.load(self.config_path)
        for item in self.storage_tree.get_children():
            self.storage_tree.delete(item)
        all_chs = self.config.get_all_storage_channels()
        for i, ch in enumerate(all_chs, 1):
            role = "main" if ch == self.config.main_channel else "storage"
            self.storage_tree.insert("", tk.END, values=(i, str(ch), role))

        # Also refresh the upload destination combo box
        if hasattr(self, 'upload_channel_combo'):
            combo_values = []
            for ch in all_chs:
                if ch == self.config.main_channel:
                    combo_values.append(f"main ({ch})")
                else:
                    combo_values.append(str(ch))
            self.upload_channel_combo.config(values=combo_values)
            # If current selection is no longer valid, reset to main
            current = self.upload_channel.get()
            if current not in combo_values:
                self.upload_channel.set(combo_values[0] if combo_values else "main")

    def add_storage_channel(self):
        """Add a storage channel from the entry field."""
        ch_str = self.new_storage_channel.get().strip()
        if not ch_str:
            messagebox.showwarning("No channel", "Please enter a channel ID.")
            return
        # Parse channel ID
        try:
            ch_id = int(ch_str)
        except ValueError:
            ch_id = ch_str  # keep as @username
        self.new_storage_channel.set("")
        self.config = tgv.Config.load(self.config_path)
        if self.config.add_storage_channel(ch_id):
            self.config.save()
            self.refresh_storage_channels()
            self.status_label.config(text=f"Added storage channel: {ch_id}")
        else:
            messagebox.showwarning("Duplicate", f"Channel {ch_id} is already in the list.")

    def remove_storage_channel(self):
        """Remove the selected storage channel."""
        selected = self.storage_tree.selection()
        if not selected:
            messagebox.showwarning("No selection", "Select a channel to remove.")
            return
        vals = self.storage_tree.item(selected[0], "values")
        ch_str = vals[1]
        try:
            ch_id = int(ch_str)
        except ValueError:
            ch_id = ch_str

        if ch_id == self.config.main_channel:
            messagebox.showwarning("Cannot remove",
                "The main channel cannot be removed.\n"
                "Use 'Main channel' field above to change it.")
            return

        if not messagebox.askyesno("Remove Channel",
            f"Remove channel {ch_id} from the storage list?\n\n"
            f"Files already in this channel are NOT deleted."):
            return

        self.config = tgv.Config.load(self.config_path)
        if self.config.remove_storage_channel(ch_id):
            self.config.save()
            self.refresh_storage_channels()
            self.status_label.config(text=f"Removed storage channel: {ch_id}")

    def action_cleanup(self):
        """Cleanup temp channel."""
        if not messagebox.askyesno("Cleanup",
            "Delete recent messages from the temp channel?\n\n"
            "Use this if a previous download left forwarded messages behind."):
            return
        self._run_async("Cleaning temp channel...", ["cleanup", "--max-count", "100"],
                        callback=self._on_settings_action)

    def action_db_find(self):
        """Find DB backup in channel."""
        self._run_async("Finding DB backup...", ["db", "find"],
                        callback=self._on_settings_action)

    def action_db_verify(self):
        """Verify database integrity — check for share_link/manifest_msg_id mismatches."""
        self._run_async("Verifying database integrity...", ["db", "verify", "--force"],
                        callback=self._on_settings_action)

    def action_find_missing(self):
        """Find files in DB whose messages are missing from the channel."""
        if not messagebox.askyesno("Find Missing Files",
            "This will check each file in the database against the channel.\n\n"
            "For each file, it tries to access the manifest message. If the\n"
            "manifest is not found, the file is marked as 'corrupted'.\n\n"
            "This may take a while. Continue?"):
            return
        self._run_async("Finding missing files...", ["db", "find-missing"],
                        callback=self._on_settings_action)

    def action_clear_temp_keep_db(self):
        """Clear ALL messages from temp channel EXCEPT the DB backup."""
        if not messagebox.askyesno("Clear Temp Channel",
            "Delete ALL messages from the temp channel EXCEPT the database backup?\n\n"
            "This is useful for cleaning up stale forwarded messages.\n\n"
            "Continue?"):
            return
        self._run_async("Clearing temp channel (keeping DB)...",
                        ["db", "clear-temp"],
                        callback=self._on_settings_action)

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
            messagebox.showwarning("No token", "Please enter a bot token.")
            return
        # Clear the entry immediately so user can add another
        self.new_token.set("")
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
            tg_script = _resolve_tg_script()
            if tg_script is not None:
                cmd = [sys.executable, tg_script, "--config", self.config_path, "setup"]
            else:
                cmd = [sys.executable, "-m", "tg_vault", "--config", self.config_path, "setup"]
            try:
                subprocess.run(cmd, cwd=_PROJECT_ROOT)
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
        self.refresh_bot_list()
        # Reload form fields too — bot add/remove changes config
        self.load_configuration()
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
        # Count orphans if DB is enabled
        orphan_info = ""
        if self.config.db_enabled:
            try:
                db = self.config.get_db()
                if db is not None:
                    n = db.orphan_count()
                    if n > 0:
                        orphan_info = f" | 👻 {n}"
            except Exception:
                pass
        self.root.title(f"tg-vault — Telegram Cloud Storage | Bots: {bot_count} | DB: {db_status}{proxy_info}{orphan_info}")

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
