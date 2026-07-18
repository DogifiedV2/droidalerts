from __future__ import annotations

import json
import multiprocessing
import os
import csv
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from queue import Empty as QueueEmpty
from tkinter import BooleanVar, DoubleVar, IntVar, StringVar, filedialog

import tkinter as tk
import cv2

try:
    import ttkbootstrap as ttk

    BOOTSTRAP = True
except Exception:
    from tkinter import ttk

    BOOTSTRAP = False

from . import __version__
from .alerts import AlertPolicy
from .belt.names import DROID_NAMES as BELT_DROID_NAMES
from .belt.dev_logging import belt_dev_dir
from .belt.overlay import BeltOverlay
from .belt.region import RelativeRegion as BeltRelativeRegion
from .belt.region import load_region as load_belt_region
from .belt.region import save_region as save_belt_region
from .belt.sample_collection import belt_template_samples_dir
from .belt.selector import RegionSelector as BeltRegionSelector
from .belt.targets import (
    BELT_FAMILY_ORDER,
    BELT_TARGET_FAMILIES_BY_LABEL,
    BELT_TARGET_LABELS,
    belt_target_label,
    is_belt_alert_target,
    normalize_belt_target_tiers,
)
from .belt.worker import run_belt_worker_process
from .capture import (
    MonitorDescriptor,
    MonitorInfo,
    PixelBox,
    create_capture,
    format_monitor_label,
    format_tk_geometry,
    list_monitors,
    set_dpi_awareness,
)
from .classifier import Detection
from .config import (
    AppConfig,
    assets_dir,
    config_dir,
    load_config,
    normalize_belt_scan_fps,
    project_root,
    save_config,
    user_sounds_dir,
)
from .diagnostics import create_support_bundle
from .device_capture import (
    CaptureDeviceDescriptor,
    DeviceCaptureSession,
    device_capture_key,
    list_capture_devices,
    session_from_config,
)
from .logging_io import alert_samples_dir, append_event, debug_dir, logs_dir, timestamp
from .maintenance import (
    cleanup_runtime_data,
    clear_debug_captures,
    clear_history,
    format_bytes,
    storage_summary,
)
from .notifications import (
    check_for_update,
    discord_webhook_configured,
    event_text,
    load_discord_webhook,
    load_phone_alert_credentials,
    load_ntfy_token,
    ntfy_configured,
    phone_alerts_configured,
    save_discord_webhook,
    save_ntfy_token,
    save_phone_credentials,
    send_discord_alert,
    send_ntfy_alert,
    send_ntfy_test_alert,
    send_phone_alert,
    send_phone_test_alert,
    valid_ntfy_server_url,
    valid_ntfy_topic,
    valid_discord_webhook_url,
)
from .popup import popup_icon_path, show_popup
from .region import Calibration, RegionResolver
from .telemetry import (
    AnonymousAppTelemetryClient,
    AnonymousBeltTelemetryClient,
    load_or_create_anonymous_install_id,
)
from .timers import format_countdown, seconds_until_next
from .ui_theme import (
    DEFAULT_THEME_KEY,
    apply_app_theme,
    normalize_theme_key,
    theme_for,
    theme_label,
    theme_labels,
)
from .watcher import run_watch
from .window_capture import (
    WINDOW_CAPTURE_EXPLANATION,
    WindowDescriptor,
    list_capture_windows,
    window_capture_key,
)


ALERT_COMBOS: tuple[tuple[str, str], ...] = (
    ("Rainbow", "Epic"),
    ("Rainbow", "Legendary"),
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Mythic"),
)
UPDATE_POLL_INTERVAL_MS = 15 * 60 * 1000
DISCORD_COMMUNITY_URL = "https://discord.gg/ZmFPjS4784"
IDENTIFY_INSTALL_URL = "https://gonk.tools/identify"
DEFAULT_WINDOW_WIDTH = 1400
DEFAULT_WINDOW_HEIGHT = 1040
PRIORITY_DIALOG_WIDTH = 760
PRIORITY_DIALOG_HEIGHT = 820
BELT_REGION_INSTRUCTIONS = (
    "Officially supported setup: stand at the start of the belt and match the guide with two "
    "complete blueprints visible. Price labels may be inside the box; they are ignored. Other "
    "camera angles and framing are not officially supported."
)


def bootstyle(value: str) -> dict[str, str]:
    return {"bootstyle": value} if BOOTSTRAP else {}


def muted_style() -> dict[str, str]:
    return {"style": "Muted.TLabel"}


def fit_window_size(
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    *,
    horizontal_margin: int,
    vertical_margin: int,
) -> tuple[int, int]:
    """Fit a preferred window size inside the usable display area."""
    usable_width = max(1, int(screen_width) - horizontal_margin)
    usable_height = max(1, int(screen_height) - vertical_margin)
    return min(int(width), usable_width), min(int(height), usable_height)


def make_root(ui_theme: str = DEFAULT_THEME_KEY) -> tk.Tk:
    if BOOTSTRAP:
        root = ttk.Window(themename="darkly")
    else:
        root = tk.Tk()
    apply_app_theme(
        ttk.Style(),
        ui_theme,
        bootstrap=BOOTSTRAP,
    )
    return root


def read_last_lines(path: Path, *, max_lines: int, chunk_bytes: int = 2_000_000) -> list[str]:
    """Read only the file tail so huge event logs never stall the UI thread."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - chunk_bytes))
        data = fh.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > chunk_bytes and lines:
        lines = lines[1:]
    return lines[-max_lines:]


def clamp_dialog_position(
    x: int,
    y: int,
    width: int,
    height: int,
    monitors: list[MonitorDescriptor],
) -> tuple[int, int]:
    """Keep a dialog on its nearest physical monitor, including negative coordinates."""
    if not monitors:
        return int(x), int(y)

    center_x = x + width / 2
    center_y = y + height / 2

    def distance_squared(monitor: MonitorDescriptor) -> float:
        right = monitor.left + monitor.width
        bottom = monitor.top + monitor.height
        dx = max(monitor.left - center_x, 0.0, center_x - right)
        dy = max(monitor.top - center_y, 0.0, center_y - bottom)
        return dx * dx + dy * dy

    monitor = min(monitors, key=distance_squared)
    max_x = monitor.left + max(0, monitor.width - width)
    max_y = monitor.top + max(0, monitor.height - height)
    return (
        max(monitor.left, min(int(x), max_x)),
        max(monitor.top, min(int(y), max_y)),
    )


class DroidAlertsApp:
    def __init__(self, root: tk.Tk, *, config: AppConfig | None = None) -> None:
        self.root = root
        self.root.title("Droid Alerts")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.config = config or load_config()
        self._font_families = self._resolve_font_families()
        self.current_theme = apply_app_theme(
            ttk.Style(),
            self.config.ui_theme,
            bootstrap=BOOTSTRAP,
            font_family=self._font_families["ui"],
        )
        try:
            self.root.configure(background=self.current_theme.colors["bg"])
        except tk.TclError:
            pass
        self.app_icon: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        self._load_app_icon()
        self.watch_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self._watch_stop_reason = ""
        self._watcher_header_state = "Stopped"
        self.belt_process = None
        self.belt_stop_event = None
        self.belt_status_queue = None
        self.belt_telemetry: AnonymousBeltTelemetryClient | None = None
        self.device_capture_session: DeviceCaptureSession | None = None
        self.app_telemetry = AnonymousAppTelemetryClient(self.config)
        self._belt_poll_after_id: str | None = None
        self._belt_worker_ready = False
        self._belt_stop_reason = ""
        self._belt_error_message = ""
        self._belt_restart_after_stop = False
        self._belt_header_state = "Stopped"
        self._shutting_down = False
        self.belt_region: PixelBox | None = None
        self.belt_selector = None
        self._belt_selector_root_state: str | None = None
        self.belt_overlay = BeltOverlay(self.root)
        # Loading settings at application startup resolves the default region,
        # but the overlay is opt-in until the user opens Belt setup or starts
        # tracking. This keeps normal chat-only launches unobstructed.
        self._belt_overlay_requested = False
        self._belt_visible_tracks: list[dict[str, object]] = []
        self._belt_overlay_scale: tuple[float, float] = (1.0, 1.0)
        self.region_overlay: tk.Toplevel | None = None
        self.region_overlay_windows: list[tk.Toplevel] = []
        self.region_positioner: tk.Toplevel | None = None
        self.droid_timers = None
        self.region_box: PixelBox | None = None
        self.region_source: str = ""
        self.region_screen_size: tuple[int, int] | None = None
        self.region_monitor_offset: tuple[int, int] = (0, 0)
        self.region_monitor_key: str | None = None
        self.update_check_running = False
        self.available_update: dict[str, str] | None = None
        self._update_poll_after_id: str | None = None
        self._log_file_signature: tuple[int, int] | None = None
        self._log_refresh_after_id: str | None = None
        self._autosave_after_id: str | None = None
        self._loading_settings = False
        self._autosave_ready = False
        self.share_debug_detections_check = None
        self.timer_reminders_check = None
        self.belt_tab_button = None
        self.history_tab_button = None
        self.belt_dev_mode_check = None
        self.belt_template_collection_check = None
        self.session_detection_count = 0
        self.session_alert_count = 0
        self.session_monitoring_seconds = 0.0
        self._watch_segment_started: float | None = None
        self._dashboard_timer_after_id: str | None = None
        self._storage_after_id: str | None = None
        self.history_rows_by_item: dict[str, dict[str, object]] = {}
        self._last_cleanup_at = 0.0

        self.status_var = StringVar(value="Stopped")
        self.sidebar_status_var = StringVar(value="●  Stopped")
        self.page_title_var = StringVar(value="Dashboard")
        self.detail_var = StringVar(value="Ready")
        self.region_status_var = StringVar(value="")
        self.watcher_status_var = StringVar(value="Ready to watch")
        self.watcher_detail_var = StringVar(value="Choose the display with Fortnite, then start watching.")
        self.last_scan_var = StringVar(value="No scans yet")
        self.last_alert_var = StringVar(value="No priority alerts this session")
        self.session_stats_var = StringVar(value="0 detections · 0 alerts")
        self.belt_status_var = StringVar(value="Ready to track")
        self.belt_detail_var = StringVar(value=BELT_REGION_INSTRUCTIONS)
        self.belt_region_var = StringVar(value="No belt region selected for this display")
        self.belt_targets_var = StringVar(value="None selected")
        self.belt_tracks_var = StringVar(value="0 active tracks")
        self.belt_last_scan_var = StringVar(value="No belt scans yet")
        self.belt_samples_var = StringVar(value="Template collection is off")
        self.belt_priority_tree = None
        self.storage_status_var = StringVar(value="Calculating storage…")
        self.channel_status_vars = {
            "Popup": StringVar(value="Ready"),
            "Sound": StringVar(value="Ready"),
            "Discord": StringVar(value="Not configured"),
            "ntfy": StringVar(value="Not configured"),
            "Pushover": StringVar(value="Not configured"),
        }
        self.timer_vars = {
            "beskar": StringVar(value="--:--"),
            "mythic": StringVar(value="--:--"),
            "rainbow": StringVar(value="--:--"),
        }
        self.setting_vars: dict[str, object] = {"monitor_index": IntVar(value=1)}
        self.alert_vars: dict[tuple[str, str], BooleanVar] = {}
        self.advanced_widgets: list[object] = []
        self.monitor_display_var = StringVar(value="Monitor 1")
        self.capture_source_var = StringVar(value="Both watchers: Monitor 1")
        self.monitor_indexes_by_label: dict[str, int] = {}
        self.options_outer = None
        self.options_canvas: tk.Canvas | None = None
        self.options_canvas_window: int | None = None
        self.options_scrollbar = None
        self._options_content_width: int | None = None
        self._options_scrollregion_bounds: tuple[int, int, int, int] | None = None
        self._options_scrollregion_after_id: str | None = None
        self._macos_repaint_after_id: str | None = None
        self.scrollable_pages: dict[object, tuple[tk.Canvas, object, int]] = {}

        self._build_ui()
        self.app_telemetry.start()
        self.load_settings()
        self._apply_initial_geometry()
        self._wire_auto_save()
        if self.config.droid_timers_enabled:
            self.show_droid_timers()
        self.refresh_logs()
        self._schedule_log_refresh()
        self.root.after(700, self.run_first_time_intro)
        self.root.after(1100, self.offer_discord_community)
        self._update_poll_after_id = self.root.after(1500, self._poll_for_updates)
        self.root.after(200, self._update_dashboard_timers)
        self.root.after(500, self._refresh_storage_status)
        self.root.after(800, self._start_runtime_features)

    def _apply_initial_geometry(self) -> None:
        # Size the window from measured content so Windows DPI scaling and
        # different font metrics never push controls out of view.
        self.root.update_idletasks()
        required_width = self.root.winfo_reqwidth() + 20
        required_height = self.root.winfo_reqheight() + 20
        width, height = fit_window_size(
            max(DEFAULT_WINDOW_WIDTH, required_width),
            max(DEFAULT_WINDOW_HEIGHT, required_height),
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            horizontal_margin=80,
            vertical_margin=140,
        )
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(940, width), min(650, height))

    def _resolve_font_families(self) -> dict[str, str]:
        # Segoe UI / Consolas only exist on Windows; macOS silently substitutes
        # mismatched fallbacks, so resolve the platform families once.
        if sys.platform != "darwin":
            return {"ui": "Segoe UI", "mono": "Consolas"}
        from tkinter import font as tkfont

        try:
            base = tkfont.nametofont("TkDefaultFont", self.root).actual("family")
        except Exception:
            base = "Helvetica Neue"
        return {"ui": base, "mono": "Menlo"}

    def _font(self, size: int, weight: str = "", *, mono: bool = False) -> tuple:
        family = self._font_families["mono" if mono else "ui"]
        return (family, size, weight) if weight else (family, size)

    @staticmethod
    def _autowrap(label, container, *, pad: int = 28) -> None:
        def apply(event) -> None:
            label.configure(wraplength=max(240, event.width - pad))

        container.bind("<Configure>", apply, add="+")

    def _load_app_icon(self) -> None:
        path = popup_icon_path(self.config)
        if path.exists():
            try:
                self.app_icon = tk.PhotoImage(file=str(path))
                self.root.iconphoto(True, self.app_icon)
                max_dim = max(self.app_icon.width(), self.app_icon.height())
                factor = max(1, (max_dim + 47) // 48)
                self.header_icon = self.app_icon.subsample(factor, factor)
            except Exception as exc:
                print(f"[GUI] Failed to load app icon: {exc}")

        # Tk's PNG iconphoto is sufficient for the in-app brand image, but
        # Windows title bars and taskbar grouping require a native ICO.
        if sys.platform == "win32":
            icon_path = assets_dir() / "signals_icon.ico"
            if icon_path.exists():
                try:
                    self.root.iconbitmap(str(icon_path), default=str(icon_path))
                except tk.TclError as exc:
                    print(f"[GUI] Failed to load Windows app icon: {exc}")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Page.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(
            outer,
            width=214,
            padding=(14, 18, 14, 16),
            style="Sidebar.TFrame",
        )
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)

        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=4,
            pady=(0, 26),
        )
        brand.columnconfigure(1, weight=1)
        if self.header_icon is not None:
            ttk.Label(brand, image=self.header_icon, style="Sidebar.TLabel").grid(
                row=0,
                column=0,
                rowspan=2,
                sticky="w",
                padx=(0, 10),
            )
        title_column = 1 if self.header_icon is not None else 0
        ttk.Label(
            brand,
            text="Droid Alerts",
            font=self._font(13, "bold"),
            style="Sidebar.TLabel",
        ).grid(
            row=0,
            column=title_column,
            sticky="sw",
        )
        ttk.Label(
            brand,
            text=f"v{__version__}",
            style="SidebarMuted.TLabel",
        ).grid(
            row=1,
            column=title_column,
            sticky="nw",
        )

        def hide_native_tabs() -> None:
            # Navigation lives in the sidebar. An empty tab layout keeps the
            # Notebook useful as a page stack without rendering a second nav.
            try:
                style = ttk.Style()
                style.layout("TNotebook.Tab", [])
                style.configure("TNotebook", borderwidth=0, tabmargins=0)
            except Exception:
                pass

        hide_native_tabs()
        main = ttk.Frame(
            outer,
            padding=(26, 20, 26, 16),
            style="Page.TFrame",
        )
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        header = ttk.Frame(main, style="Page.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.page_title_var, style="PageTitle.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self.update_ready_button = ttk.Button(
            header,
            text="Update ready",
            command=self.show_available_update,
            **bootstyle("success"),
        )
        self.update_ready_button.grid(row=0, column=1, sticky="e")
        self.update_ready_button.grid_remove()

        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.root.after_idle(hide_native_tabs)

        self.dashboard_tab = ttk.Frame(self.notebook, padding=(2, 2, 2, 6), style="Page.TFrame")
        self.belt_tab = ttk.Frame(self.notebook, padding=(2, 2, 2, 6), style="Page.TFrame")
        self.logs_tab = ttk.Frame(self.notebook, padding=(2, 2, 2, 6), style="Page.TFrame")
        self.files_tab = ttk.Frame(self.notebook, padding=(2, 2, 2, 6), style="Page.TFrame")
        self.settings_tab = ttk.Frame(self.notebook, padding=(2, 2, 2, 6), style="Page.TFrame")
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.belt_tab, text="Belt Tracker")
        self.notebook.add(self.logs_tab, text="History")
        self.notebook.add(self.files_tab, text="Diagnostics")
        self.notebook.add(self.settings_tab, text="Settings")
        self.dashboard_content = self._create_scrollable_page(self.dashboard_tab)
        self.belt_content = self._create_scrollable_page(self.belt_tab)

        nav = ttk.Frame(sidebar, style="Sidebar.TFrame")
        nav.grid(row=1, column=0, sticky="new")
        nav.columnconfigure(0, weight=1)
        page_items = (
            ("Dashboard", self.dashboard_tab),
            ("Belt Tracker", self.belt_tab),
            ("History", self.logs_tab),
            ("Diagnostics", self.files_tab),
            ("Settings", self.settings_tab),
        )
        self.page_metadata = {tab: title for title, tab in page_items}
        self.tab_buttons: list[tuple[object, object]] = []
        for row, (text, tab) in enumerate(page_items):
            button = ttk.Button(
                nav,
                text=text,
                command=lambda selected=tab: self.notebook.select(selected),
                style="Sidebar.TButton",
            )
            button.grid(row=row, column=0, sticky="ew", pady=2)
            self.tab_buttons.append((button, tab))
            if tab is self.belt_tab:
                self.belt_tab_button = button
            elif tab is self.logs_tab:
                self.history_tab_button = button

        status = ttk.Frame(sidebar, style="Sidebar.TFrame")
        status.grid(row=3, column=0, sticky="sew", padx=5)
        ttk.Label(status, text="APP STATUS", style="SidebarMuted.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self.header_status_label = ttk.Label(
            status,
            textvariable=self.sidebar_status_var,
            style="SidebarStatus.TLabel",
        )
        self.header_status_label.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self._refresh_header_status()
        self.notebook.bind("<<NotebookTabChanged>>", self._highlight_active_tab, add="+")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_belt_tab_opened, add="+")
        self._highlight_active_tab()

        self._build_dashboard_tab()
        self._build_belt_tab()
        self._build_logs_tab()
        self._build_files_tab()
        self._build_settings_tab()
        self.root.bind_all("<MouseWheel>", self._on_page_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_page_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_page_mousewheel, add="+")
        self._wire_macos_repaint_workaround()

        feedback = ttk.Frame(
            main,
            padding=(2, 10, 2, 0),
            style="Page.TFrame",
        )
        feedback.grid(row=2, column=0, sticky="ew")
        feedback.columnconfigure(0, weight=1)
        ttk.Label(
            feedback,
            textvariable=self.detail_var,
            anchor="w",
            style="Muted.TLabel",
        ).grid(
            row=0,
            column=0,
            sticky="ew",
        )

    def _create_scrollable_page(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            tab,
            background=self.current_theme.colors["bg"],
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        scrollbar.grid_remove()
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(
            canvas,
            padding=(2, 2, 10, 10),
            style="Page.TFrame",
        )
        window = canvas.create_window((0, 0), anchor="nw", window=content)

        def update_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            if content.winfo_reqheight() > canvas.winfo_height() + 1:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
                canvas.yview_moveto(0)

        def resize_content(event) -> None:
            canvas.itemconfigure(window, width=max(1, event.width))
            canvas.after_idle(update_scrollregion)

        content.bind("<Configure>", update_scrollregion, add="+")
        canvas.bind("<Configure>", resize_content, add="+")
        self.scrollable_pages[tab] = (canvas, scrollbar, window)
        return content

    def _on_page_mousewheel(self, event):
        try:
            selected = self.root.nametowidget(self.notebook.select())
            canvas, _scrollbar, _window = self.scrollable_pages[selected]
        except Exception:
            return None
        x, y = self.root.winfo_pointerxy()
        if not (
            canvas.winfo_rootx() <= x < canvas.winfo_rootx() + canvas.winfo_width()
            and canvas.winfo_rooty() <= y < canvas.winfo_rooty() + canvas.winfo_height()
        ):
            return None
        top, bottom = canvas.yview()
        if top <= 0.0 and bottom >= 1.0:
            return None
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            raw_delta = int(getattr(event, "delta", 0))
            delta = -1 if raw_delta > 0 else 1
        canvas.yview_scroll(delta * 3, "units")
        return "break"

    def _labeled_section(self, parent, text: str):
        outer = ttk.Frame(parent, padding=(18, 15), style="Card.TFrame")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        ttk.Label(
            outer,
            text=text.strip().title(),
            style="SectionTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        inner = ttk.Frame(outer, padding=(0, 11, 0, 0))
        inner.grid(row=1, column=0, sticky="nsew")
        return outer, inner

    def _link_label(self, parent, text: str, command, *, style: str = "Link.TButton"):
        return ttk.Button(
            parent,
            text=text,
            command=command,
            cursor="hand2",
            style=style,
        )

    def on_theme_selected(self, _event=None) -> None:
        selected = normalize_theme_key(self._value("ui_theme"))
        self._apply_theme(selected, announce=True)
        self._schedule_auto_save(delay_ms=80)

    def _apply_theme(self, value: str, *, announce: bool) -> None:
        self.current_theme = apply_app_theme(
            ttk.Style(),
            value,
            bootstrap=BOOTSTRAP,
            font_family=self._font_families["ui"],
        )
        self.config.ui_theme = self.current_theme.key
        self._set_var("ui_theme", self.current_theme.label)
        try:
            self.root.configure(background=self.current_theme.colors["bg"])
        except tk.TclError:
            pass
        if self.options_canvas is not None:
            self.options_canvas.configure(background=self.current_theme.colors["bg"])
        for canvas, _scrollbar, _window in self.scrollable_pages.values():
            canvas.configure(background=self.current_theme.colors["bg"])
        try:
            style = ttk.Style()
            style.layout("TNotebook.Tab", [])
            style.configure("TNotebook", borderwidth=0, tabmargins=0)
        except Exception:
            pass
        self._configure_history_tags()
        self._highlight_active_tab()
        self._apply_watcher_status_style(self.status_var.get())
        if announce:
            self.detail_var.set(f"Theme changed to {self.current_theme.label}")
        self.root.after_idle(self._force_macos_repaint)

    def _configure_history_tags(self) -> None:
        if not hasattr(self, "logs_tree"):
            return
        colors = self.current_theme.colors
        self.logs_tree.tag_configure("success", foreground=colors["success"])
        self.logs_tree.tag_configure("failure", foreground=colors["danger"])
        self.logs_tree.tag_configure("priority", foreground=colors["primary"])
        self.logs_tree.tag_configure("muted", foreground=self.current_theme.muted_fg)

    def _build_dashboard_tab(self) -> None:
        page = self.dashboard_content
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2)
        page.rowconfigure(1, weight=1)

        hero_outer, hero = self._labeled_section(page, "MONITORING")
        hero_outer.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        hero.columnconfigure(0, weight=1)
        status_block = ttk.Frame(hero)
        status_block.grid(row=0, column=0, sticky="ew")
        status_block.columnconfigure(0, weight=1)
        ttk.Label(
            status_block,
            textvariable=self.watcher_status_var,
            font=self._font(18, "bold"),
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )
        watcher_detail = ttk.Label(
            status_block,
            textvariable=self.watcher_detail_var,
            wraplength=650,
            justify="left",
            **muted_style(),
        )
        watcher_detail.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self._autowrap(watcher_detail, status_block)

        self.watch_button = ttk.Button(
            hero,
            text="Start Watching",
            command=self.toggle_watcher,
            padding=(18, 11),
            **bootstyle("success"),
        )
        self.watch_button.grid(row=0, column=1, sticky="ne", padx=(24, 0))

        ttk.Separator(hero, orient="horizontal").grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(16, 14),
        )

        source_panel = ttk.Frame(
            hero,
            padding=(14, 12),
            style="Subtle.TFrame",
        )
        source_panel.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            source_panel,
            text="Capture source",
            style="Subtle.TLabel",
            font=self._font(10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        display_group = ttk.Frame(source_panel, style="Subtle.TFrame")
        display_group.grid(row=1, column=0, sticky="nw", pady=(10, 0))
        ttk.Label(
            display_group,
            text="Game display",
            style="SubtleMuted.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.monitor_combobox = ttk.Combobox(
            display_group,
            textvariable=self.monitor_display_var,
            state="readonly",
            width=34,
            postcommand=self.refresh_monitor_choices,
        )
        self.monitor_combobox.grid(row=1, column=0, sticky="w")
        self.monitor_combobox.bind("<<ComboboxSelected>>", self.on_monitor_selected)
        ttk.Button(
            display_group,
            text="Identify",
            command=self.identify_displays,
            style="Utility.TButton",
        ).grid(row=1, column=1, sticky="w", padx=(8, 0))

        alternate_sources = ttk.Frame(source_panel, style="Subtle.TFrame")
        alternate_sources.grid(row=1, column=1, sticky="nw", padx=(24, 0), pady=(10, 0))
        ttk.Label(
            alternate_sources,
            text="Other sources",
            style="SubtleMuted.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        ttk.Button(
            alternate_sources,
            text="Select Window",
            command=self.select_capture_window,
            style="Utility.TButton",
        ).grid(row=1, column=0, padx=(0, 8))
        ttk.Button(
            alternate_sources,
            text="Capture Device",
            command=self.select_capture_device,
            style="Utility.TButton",
        ).grid(row=1, column=1)
        source_summary = ttk.Label(
            source_panel,
            textvariable=self.capture_source_var,
            wraplength=560,
            justify="left",
            style="SubtleMuted.TLabel",
        )
        source_summary.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._autowrap(source_summary, source_panel, pad=28)
        self.refresh_monitor_choices()

        alerts_panel = ttk.Frame(page)
        alerts_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        alerts_panel.columnconfigure(0, weight=1)
        alerts_panel.rowconfigure(1, weight=1)
        self._build_priority_alerts(alerts_panel, row=0)
        self._build_alert_channels(alerts_panel, row=1)

        right_panel = ttk.Frame(page)
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)

        glance_outer, glance = self._labeled_section(right_panel, "NEXT SPAWNS")
        glance_outer.grid(row=0, column=0, sticky="new", pady=(0, 16))
        glance.columnconfigure(0, weight=1)
        timer_labels = (("beskar", "Beskar"), ("mythic", "Mythic"), ("rainbow", "Rainbow"))
        for row, (key, label) in enumerate(timer_labels, start=1):
            ttk.Label(glance, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(glance, textvariable=self.timer_vars[key], font=self._font(12, "bold", mono=True)).grid(
                row=row, column=1, sticky="e", pady=3
            )
        self.setting_vars["droid_timers_enabled"] = BooleanVar(value=False)
        self.setting_vars["timer_reminders_enabled"] = BooleanVar(value=False)
        ttk.Checkbutton(
            glance,
            text="Show Droid Timers overlay",
            variable=self.setting_vars["droid_timers_enabled"],
            command=self.on_droid_timers_toggle,
            **bootstyle("round-toggle"),
        ).grid(row=4, column=0, sticky="w", pady=(8, 2))
        self._link_label(glance, "Adjust Timer Position", self.adjust_droid_timers).grid(
            row=4, column=1, sticky="e", pady=(8, 2)
        )
        self.timer_reminders_check = ttk.Checkbutton(
            glance,
            text="Timer reminder sound",
            variable=self.setting_vars["timer_reminders_enabled"],
            **bootstyle("round-toggle"),
        )
        self.timer_reminders_check.grid(row=5, column=0, columnspan=2, sticky="w", padx=(18, 0), pady=(2, 0))
        self.timer_reminders_check.grid_remove()

        session_outer, session = self._labeled_section(right_panel, "SESSION")
        session_outer.grid(row=1, column=0, sticky="new")
        session.columnconfigure(0, weight=1)
        ttk.Label(session, textvariable=self.session_stats_var).grid(
            row=0, column=0, sticky="w", pady=(0, 2)
        )
        ttk.Label(session, textvariable=self.last_scan_var, **muted_style()).grid(
            row=1, column=0, sticky="w", pady=2
        )
        last_alert_label = ttk.Label(
            session,
            textvariable=self.last_alert_var,
            wraplength=310,
            justify="left",
        )
        last_alert_label.grid(row=2, column=0, sticky="w", pady=(2, 12))
        self._autowrap(last_alert_label, session)
        ttk.Button(session, text="Test All Alerts", command=self.send_test_alert, **bootstyle("info-outline")).grid(
            row=3, column=0, sticky="ew"
        )

    def _build_priority_alerts(self, parent, *, row: int) -> None:
        alerts_outer, alerts = self._labeled_section(parent, "PRIORITY ALERTS")
        alerts_outer.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        alerts.columnconfigure(0, weight=1)
        alerts.columnconfigure(1, weight=1)
        for index, combo in enumerate(ALERT_COMBOS):
            var = BooleanVar(value=True)
            self.alert_vars[combo] = var
            ttk.Checkbutton(
                alerts,
                text=f"{combo[0]} {combo[1]}",
                variable=var,
                **bootstyle("round-toggle"),
            ).grid(row=index // 2, column=index % 2, sticky="w", pady=5)

    def _build_alert_channels(self, parent, *, row: int) -> None:
        channels_outer, channels = self._labeled_section(parent, "ALERT CHANNELS")
        channels_outer.grid(row=row, column=0, sticky="new")
        channels.columnconfigure(0, minsize=118)
        channels.columnconfigure(1, weight=1)
        channels.columnconfigure(2, minsize=64)

        for key in (
            "popup_enabled",
            "sound_enabled",
            "discord_enabled",
            "ntfy_enabled",
            "phone_alerts_enabled",
        ):
            self.setting_vars[key] = BooleanVar(value=False)

        channel_rows = (
            ("Popup", "popup_enabled", None, "popup"),
            ("Sound", "sound_enabled", None, "sound"),
            ("Discord", "discord_enabled", self.setup_discord_alerts_and_enable, "discord"),
            ("ntfy", "ntfy_enabled", self.setup_ntfy_alerts_and_enable, "ntfy"),
            ("Pushover", "phone_alerts_enabled", self.setup_phone_alerts_and_enable, "pushover"),
        )
        for row, (label, key, setup, channel) in enumerate(channel_rows):
            command = {
                "discord_enabled": self.on_discord_alert_toggle,
                "ntfy_enabled": self.on_ntfy_alert_toggle,
                "phone_alerts_enabled": self.on_phone_alert_toggle,
            }.get(key)
            ttk.Checkbutton(
                channels,
                text=label,
                variable=self.setting_vars[key],
                command=command,
                **bootstyle("round-toggle"),
            ).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(channels, textvariable=self.channel_status_vars[label], **muted_style()).grid(
                row=row, column=1, sticky="w", padx=(10, 16)
            )
            if setup is not None:
                self._link_label(channels, "Set up", setup, style="Utility.TButton").grid(
                    row=row, column=2, sticky="e", padx=(0, 16)
                )
            self._link_label(
                channels,
                "Test",
                lambda selected=channel: self.send_channel_test(selected),
                style="Utility.TButton",
            ).grid(row=row, column=3, sticky="e")

    def _build_alert_appearance(self, parent, *, row: int) -> None:
        appearance_outer, appearance = self._labeled_section(parent, "ALERT APPEARANCE")
        appearance_outer.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        appearance.columnconfigure(1, weight=1)
        self.setting_vars["popup_seconds"] = DoubleVar(value=8.0)
        self.setting_vars["popup_position"] = StringVar(value="Top center")
        self.setting_vars["popup_scale"] = DoubleVar(value=1.0)
        self.setting_vars["popup_opacity"] = DoubleVar(value=1.0)
        self.setting_vars["sound_file"] = StringVar(value="")
        appearance_fields = (
            ("Popup duration", "popup_seconds", None),
            ("Popup position", "popup_position", ("Top center", "Top left", "Top right", "Bottom left", "Bottom right")),
            ("Popup size", "popup_scale", ("0.75", "1.0", "1.25", "1.5")),
            ("Popup opacity", "popup_opacity", ("0.65", "0.8", "1.0")),
        )
        for row, (label, key, values) in enumerate(appearance_fields):
            ttk.Label(appearance, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if values is None:
                widget = ttk.Spinbox(
                    appearance,
                    textvariable=self.setting_vars[key],
                    from_=1.0,
                    to=30.0,
                    increment=0.5,
                    width=16,
                )
            else:
                widget = ttk.Combobox(
                    appearance,
                    textvariable=self.setting_vars[key],
                    values=values,
                    state="readonly",
                    width=18,
                )
            widget.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Label(appearance, text="Alert sound").grid(row=4, column=0, sticky="w", pady=4)
        self.sound_combobox = ttk.Combobox(
            appearance,
            textvariable=self.setting_vars["sound_file"],
            state="readonly",
            width=28,
        )
        self.sound_combobox.grid(row=4, column=1, sticky="w", padx=(10, 0), pady=4)
        ttk.Button(appearance, text="Add WAV…", command=self.add_alert_sound).grid(row=4, column=2, padx=(8, 0))
        self.refresh_sound_choices()

    def _build_belt_tab(self) -> None:
        page = self.belt_content
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2)
        page.rowconfigure(1, weight=1)

        tracking_outer, tracking = self._labeled_section(page, "TRACKING")
        tracking_outer.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        tracking.columnconfigure(0, weight=1)
        ttk.Label(
            tracking,
            textvariable=self.belt_status_var,
            font=self._font(18, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            tracking,
            text="FAQ",
            command=self.show_belt_faq,
            style="Utility.TButton",
        ).grid(row=0, column=1, sticky="e")
        belt_detail = ttk.Label(
            tracking,
            textvariable=self.belt_detail_var,
            wraplength=620,
            justify="left",
            **muted_style(),
        )
        belt_detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 2))
        self._autowrap(belt_detail, tracking)

        controls = ttk.Frame(tracking)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        controls.columnconfigure(2, weight=1)
        self.belt_watch_button = ttk.Button(
            controls,
            text="Start Tracking",
            width=18,
            command=self.toggle_belt_tracking,
            **bootstyle("success"),
        )
        self.belt_watch_button.grid(row=0, column=0, sticky="w", ipady=4)
        self.belt_region_button = ttk.Button(
            controls,
            text="Select Belt Region",
            command=self.select_belt_region,
            **bootstyle("info-outline"),
        )
        self.belt_region_button.grid(row=0, column=1, padx=(10, 0))

        priority_panel = ttk.Frame(page)
        priority_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        priority_panel.columnconfigure(0, weight=1)
        priority_panel.rowconfigure(0, weight=1)
        alerts_outer, alerts = self._labeled_section(priority_panel, "PRIORITY ALERTS")
        alerts_outer.grid(row=0, column=0, sticky="nsew")
        alerts.columnconfigure(0, weight=1)
        alerts.rowconfigure(1, weight=1)
        heading = ttk.Frame(alerts)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, textvariable=self.belt_targets_var, **muted_style()).grid(
            row=0, column=0, sticky="w"
        )
        self.belt_targets_button = ttk.Button(
            heading,
            text="Modify",
            command=self.choose_belt_targets,
            **bootstyle("info-outline"),
        )
        self.belt_targets_button.grid(row=0, column=1, sticky="e")
        self.belt_priority_tree = ttk.Treeview(
            alerts,
            columns=("droid", "minimum_tier"),
            show="headings",
            height=10,
        )
        self.belt_priority_tree.heading("droid", text="Droid")
        self.belt_priority_tree.heading("minimum_tier", text="Alert from")
        self.belt_priority_tree.column("droid", anchor="w", stretch=True, minwidth=110)
        self.belt_priority_tree.column(
            "minimum_tier",
            anchor="w",
            stretch=False,
            width=95,
        )
        priority_scroll = ttk.Scrollbar(
            alerts,
            orient="vertical",
            command=self.belt_priority_tree.yview,
        )
        self.belt_priority_tree.configure(yscrollcommand=priority_scroll.set)
        self.belt_priority_tree.grid(row=1, column=0, sticky="nsew")
        priority_scroll.grid(row=1, column=1, sticky="ns")

        view_panel = ttk.Frame(page)
        view_panel.grid(row=1, column=1, sticky="nsew")
        view_panel.columnconfigure(0, weight=1)
        view_outer, view = self._labeled_section(view_panel, "BELT AREA")
        view_outer.grid(row=0, column=0, sticky="new")
        view.columnconfigure(0, weight=1)
        ttk.Label(view, textvariable=self.belt_region_var, font=self._font(10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.setting_vars["belt_overlay_enabled"] = BooleanVar(value=True)
        ttk.Checkbutton(
            view,
            text="Show belt overlay",
            variable=self.setting_vars["belt_overlay_enabled"],
            command=self._belt_overlay_changed,
            **bootstyle("round-toggle"),
        ).grid(row=1, column=0, sticky="w", pady=(12, 14))
        ttk.Label(
            view,
            textvariable=self.belt_tracks_var,
            font=self._font(10, "bold"),
            **bootstyle("info"),
        ).grid(row=2, column=0, sticky="w")
        ttk.Label(
            view,
            textvariable=self.belt_last_scan_var,
            wraplength=360,
            justify="left",
            **muted_style(),
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _build_logs_tab(self) -> None:
        page = self.logs_tab
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        filters = ttk.Frame(page, padding=(14, 12), style="Card.TFrame")
        filters.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        filters.columnconfigure(4, weight=1)
        self.history_filter_var = StringVar(value="All")
        self.history_search_var = StringVar(value="")
        ttk.Label(filters, text="Show").grid(row=0, column=0, padx=(0, 6))
        filter_box = ttk.Combobox(
            filters,
            textvariable=self.history_filter_var,
            values=(
                "All",
                "Priority alerts",
                "Belt Tracker",
                "Detections",
                "Delivery failures",
                "Debug",
            ),
            state="readonly",
            width=18,
        )
        filter_box.grid(row=0, column=1)
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_logs())
        ttk.Label(filters, text="Search").grid(row=0, column=2, padx=(14, 6))
        search = ttk.Entry(filters, textvariable=self.history_search_var, width=24)
        search.grid(row=0, column=3)
        search.bind("<Return>", lambda _event: self.refresh_logs())
        self.history_summary_var = StringVar(value="No history yet")
        ttk.Label(filters, textvariable=self.history_summary_var, **muted_style()).grid(
            row=0, column=4, sticky="e"
        )

        columns = ("time", "type", "droid", "rarity", "status", "info")
        self.logs_tree = ttk.Treeview(page, columns=columns, show="headings", height=18)
        headings = {
            "time": "Time",
            "type": "Event",
            "droid": "Droid",
            "rarity": "Rarity",
            "status": "Status",
            "info": "Details",
        }
        widths = {
            "time": 120,
            "type": 95,
            "droid": 80,
            "rarity": 80,
            "status": 80,
            "info": 190,
        }
        for column in columns:
            anchor = "center" if column in {"type", "status"} else "w"
            self.logs_tree.heading(column, text=headings[column], anchor=anchor)
            self.logs_tree.column(
                column,
                width=widths[column],
                minwidth=65,
                anchor=anchor,
                stretch=column == "info",
            )
        self._configure_history_tags()
        self.logs_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=self.logs_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.logs_tree.configure(yscrollcommand=scrollbar.set)
        self.logs_tree.bind("<Double-1>", self.show_history_details)

        actions = ttk.Frame(page)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(
            actions,
            text="Refresh",
            command=self.refresh_logs,
            style="Utility.TButton",
        ).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="Export CSV",
            command=self.export_history_csv,
            **bootstyle("primary"),
        ).grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        ttk.Button(
            actions,
            text="Open Logs Folder",
            command=lambda: self.open_path(logs_dir()),
            style="Utility.TButton",
        ).grid(row=0, column=3)

    def _build_files_tab(self) -> None:
        page = self.files_tab
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)

        setup_outer, setup = self._labeled_section(page, "CHAT REGION")
        setup_outer.grid(row=0, column=0, sticky="new", padx=(0, 16))
        setup.columnconfigure(0, weight=1)
        region_intro = ttk.Label(
            setup,
            text="Confirm that the capture box covers the Droid Tycoon chat messages.",
            wraplength=410,
            justify="left",
            **muted_style(),
        )
        region_intro.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self._autowrap(region_intro, setup)
        self.region_button = ttk.Button(
            setup, text="Show Chat Region", command=self.toggle_region_overlay, width=28, **bootstyle("info")
        )
        self.region_button.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(
            setup,
            text="Move Chat Box…",
            command=self.open_region_positioner,
            width=28,
            style="Utility.TButton",
        ).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Button(
            setup,
            text="Auto Detect Region",
            command=self.auto_detect_region,
            width=28,
            style="Utility.TButton",
        ).grid(row=3, column=0, sticky="w", pady=4)
        self.region_adjust_frame = ttk.Frame(setup)
        self.region_adjust_frame.grid(row=4, column=0, sticky="w", pady=(4, 4))
        ttk.Button(self.region_adjust_frame, text="← Left", command=lambda: self.nudge_region(-10, 0), width=9).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(self.region_adjust_frame, text="↑ Up", command=lambda: self.nudge_region(0, -10), width=9).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(self.region_adjust_frame, text="↓ Down", command=lambda: self.nudge_region(0, 10), width=9).grid(
            row=0, column=2, padx=(0, 6)
        )
        ttk.Button(self.region_adjust_frame, text="Right →", command=lambda: self.nudge_region(10, 0), width=9).grid(
            row=0, column=3
        )
        ttk.Label(self.region_adjust_frame, textvariable=self.region_status_var).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(5, 0)
        )
        self.region_adjust_frame.grid_remove()

        tools_outer, tools = self._labeled_section(page, "SUPPORT & STORAGE")
        tools_outer.grid(row=0, column=1, sticky="new")
        tools.columnconfigure(0, weight=1)
        tools.columnconfigure(1, weight=1)
        storage_label = ttk.Label(tools, textvariable=self.storage_status_var, wraplength=420, justify="left")
        storage_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self._autowrap(storage_label, tools)
        ttk.Button(
            tools,
            text="Create Support Bundle",
            command=self.create_diagnostics_bundle,
            **bootstyle("info"),
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        routine_actions = (
            ("Check for Updates", lambda: self.check_updates(manual=True)),
            ("Open Data Folder", lambda: self.open_path(project_root() / "data")),
            ("Alert Samples", lambda: self.open_path(alert_samples_dir())),
            ("Debug Captures", lambda: self.open_path(debug_dir())),
        )
        for index, (label, command) in enumerate(routine_actions):
            ttk.Button(
                tools,
                text=label,
                command=command,
                style="Utility.TButton",
            ).grid(
                row=2 + index // 2,
                column=index % 2,
                sticky="ew",
                padx=(0, 8) if index % 2 == 0 else (8, 0),
                pady=4,
            )

        ttk.Separator(tools).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 12))
        ttk.Label(tools, text="Danger zone", font=self._font(10, "bold"), **bootstyle("danger")).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Button(
            tools,
            text="Clear Debug Captures",
            command=self.clear_debug_data,
            **bootstyle("danger-outline"),
        ).grid(row=6, column=0, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(
            tools,
            text="Clear History",
            command=self.clear_history_data,
            **bootstyle("danger-outline"),
        ).grid(row=6, column=1, sticky="ew", padx=(8, 0), pady=4)

    def _build_settings_tab(self) -> None:
        page = self.settings_tab
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.options_outer = ttk.Frame(page, style="Page.TFrame")
        self.options_outer.grid(row=0, column=0, sticky="nsew")
        self.options_outer.columnconfigure(0, weight=1)
        self.options_outer.rowconfigure(0, weight=1)
        canvas_background = self.current_theme.colors["bg"]
        self.options_canvas = tk.Canvas(
            self.options_outer,
            background=canvas_background,
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.options_canvas.grid(row=0, column=0, sticky="nsew")
        self.options_scrollbar = ttk.Scrollbar(self.options_outer, orient="vertical", command=self.options_canvas.yview)
        self.options_scrollbar.grid(row=0, column=1, sticky="ns")
        self.options_scrollbar.grid_remove()
        self.options_canvas.configure(yscrollcommand=self.options_scrollbar.set)
        content = ttk.Frame(self.options_canvas, padding=(2, 2, 12, 12))
        self.options_canvas_window = self.options_canvas.create_window((0, 0), anchor="nw", window=content)
        content.bind("<Configure>", self._update_options_scrollregion)
        self.options_canvas.bind("<Configure>", self._resize_options_content)
        content.columnconfigure(0, weight=1)

        self.setting_vars["advanced_mode"] = BooleanVar(value=False)
        top = ttk.Frame(content)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text="Changes save automatically.",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        advanced_toggle = ttk.Checkbutton(
            top,
            text="Advanced settings",
            variable=self.setting_vars["advanced_mode"],
            command=self.on_advanced_toggle,
            **bootstyle("round-toggle"),
        )
        if BOOTSTRAP:
            style = ttk.Style()
            base_style = advanced_toggle.cget("style")
            style.layout("Advanced.Round.Toggle", style.layout(base_style))
            style.configure("Advanced.Round.Toggle", **style.configure(base_style))
            style.configure(
                "Advanced.Round.Toggle",
                font=self._font(11, "bold"),
                padding=(4, 6),
            )
            style.map("Advanced.Round.Toggle", **style.map(base_style))
            advanced_toggle.configure(style="Advanced.Round.Toggle")
        else:
            advanced_toggle.configure(style="Advanced.TCheckbutton")
        advanced_toggle.grid(row=0, column=1, sticky="e")

        appearance_outer, appearance = self._labeled_section(content, "APPEARANCE")
        appearance_outer.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        appearance.columnconfigure(1, weight=1)
        self.setting_vars["ui_theme"] = StringVar(value=theme_label(self.config.ui_theme))
        ttk.Label(appearance, text="Theme", font=self._font(10, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
        )
        self.theme_combobox = ttk.Combobox(
            appearance,
            textvariable=self.setting_vars["ui_theme"],
            values=theme_labels(),
            state="readonly",
            width=24,
        )
        self.theme_combobox.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.theme_combobox.bind("<<ComboboxSelected>>", self.on_theme_selected)
        ttk.Label(
            appearance,
            text="Applies instantly to the app and its setup dialogs.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        behavior_outer, behavior = self._labeled_section(content, "EVERYDAY BEHAVIOUR")
        behavior_outer.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        behavior.columnconfigure(0, weight=1)
        basic_settings = (
            ("extra_checks", "Improve detection with HDR / washed-out colours", None),
            ("start_watcher_on_launch", "Start watching when Droid Alerts opens", None),
            ("update_check_enabled", "Check for updates automatically", None),
        )
        for key, _label, _command in basic_settings:
            self.setting_vars[key] = BooleanVar(value=False)
        behavior_settings = ttk.Frame(behavior)
        behavior_settings.grid(row=0, column=0, sticky="nw")
        for row, (key, label, command) in enumerate(basic_settings):
            ttk.Checkbutton(
                behavior_settings,
                text=label,
                variable=self.setting_vars[key],
                command=command,
                **bootstyle("round-toggle"),
            ).grid(row=row, column=0, sticky="w", pady=5)
        actions = ttk.Frame(behavior)
        actions.grid(row=0, column=1, sticky="ne", padx=(20, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(
            actions,
            text="What is shared?",
            command=self.show_privacy_details,
            style="Utility.TButton",
        ).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(0, 6),
        )
        ttk.Button(
            actions,
            text="Identify This Install",
            command=self.show_install_identity,
            style="Utility.TButton",
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(0, 6),
        )
        ttk.Button(
            actions,
            text="FAQ",
            command=self.show_faq,
            style="Utility.TButton",
        ).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(
            actions,
            text="Discord & Support",
            command=lambda: webbrowser.open(DISCORD_COMMUNITY_URL),
            style="Utility.TButton",
        ).grid(row=1, column=1, sticky="ew", padx=(4, 0))

        self.advanced_container = ttk.Frame(content)
        self.advanced_container.grid(row=3, column=0, sticky="ew")
        self.advanced_container.columnconfigure(0, weight=1)
        self.advanced_widgets = [self.advanced_container]

        advanced_bool_keys = (
            "save_alert_samples",
            "save_debug_screenshots",
            "share_debug_detections",
            "ntfy_include_attachment",
            "phone_include_attachment",
            "belt_dev_mode",
            "belt_template_collection_enabled",
        )
        for key in advanced_bool_keys:
            self.setting_vars[key] = BooleanVar(value=False)
        numeric_defaults = {
            "capture_interval_seconds": (DoubleVar, 0.25),
            "dedupe_seconds": (DoubleVar, 12.0),
            "alert_cooldown_seconds": (DoubleVar, 10.0),
            "validation_failures_before_calibration_prompt": (IntVar, 30),
            "retention_days": (IntVar, 30),
            "max_storage_mb": (IntVar, 500),
            "timer_reminder_seconds": (IntVar, 60),
            "timer_offset_seconds": (IntVar, 0),
            "belt_idle_scan_fps": (IntVar, 4),
            "belt_active_scan_fps": (IntVar, 8),
        }
        for key, (kind, value) in numeric_defaults.items():
            self.setting_vars[key] = kind(value=value)
        string_defaults = {
            "ntfy_server_url": "https://ntfy.sh",
            "ntfy_topic": "",
            "ntfy_priority": "5",
            "ntfy_tags": "rotating_light",
            "phone_sound": "siren",
            "update_repo": "DogifiedV2/droidalerts",
        }
        for key, value in string_defaults.items():
            self.setting_vars[key] = StringVar(value=value)

        self._build_alert_appearance(self.advanced_container, row=0)

        detector_outer, detector = self._labeled_section(self.advanced_container, "DETECTION & TIMING")
        detector_outer.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        detector.columnconfigure(1, weight=1)
        detector_fields = (
            ("Capture interval (seconds)", "capture_interval_seconds"),
            ("Duplicate window (seconds)", "dedupe_seconds"),
            ("Alert cooldown (seconds)", "alert_cooldown_seconds"),
            ("Calibration warning frames", "validation_failures_before_calibration_prompt"),
            ("Timer reminder (seconds before)", "timer_reminder_seconds"),
            ("Timer schedule offset (seconds)", "timer_offset_seconds"),
            ("Belt idle scan FPS", "belt_idle_scan_fps"),
            ("Belt active scan FPS", "belt_active_scan_fps"),
        )
        detector_ranges = {
            "capture_interval_seconds": (0.05, 5.0, 0.05),
            "dedupe_seconds": (0.0, 300.0, 1.0),
            "alert_cooldown_seconds": (0.0, 300.0, 1.0),
            "validation_failures_before_calibration_prompt": (1, 1000, 1),
            "timer_reminder_seconds": (1, 600, 5),
            "timer_offset_seconds": (-3600, 3600, 1),
            "belt_idle_scan_fps": (1, 20, 1),
            "belt_active_scan_fps": (1, 20, 1),
        }
        for row, (label, key) in enumerate(detector_fields):
            ttk.Label(detector, text=label).grid(row=row, column=0, sticky="w", pady=4)
            low, high, step = detector_ranges[key]
            ttk.Spinbox(
                detector,
                textvariable=self.setting_vars[key],
                from_=low,
                to=high,
                increment=step,
                width=16,
            ).grid(
                row=row, column=1, sticky="w", padx=(12, 24), pady=4
            )
        data_outer, data = self._labeled_section(self.advanced_container, "STORAGE & DEBUG")
        data_outer.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        data.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            data,
            text="Save alert screenshots",
            variable=self.setting_vars["save_alert_samples"],
            **bootstyle("round-toggle"),
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Checkbutton(
            data,
            text="Debug captures (Windows: numpad snapshot, macOS: every 5 seconds)",
            variable=self.setting_vars["save_debug_screenshots"],
            command=self.on_debug_mode_toggle,
            **bootstyle("round-toggle"),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.share_debug_detections_check = ttk.Checkbutton(
            data,
            text="Share alert debug screenshots with the developer",
            variable=self.setting_vars["share_debug_detections"],
            **bootstyle("round-toggle"),
        )
        self.share_debug_detections_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(data, text="Delete captures after").grid(
            row=3,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Combobox(
            data,
            textvariable=self.setting_vars["retention_days"],
            values=(0, 1, 7, 30, 90),
            state="readonly",
            width=10,
        ).grid(row=3, column=1, sticky="w", padx=(12, 0))
        ttk.Label(data, text="Storage limit (MB)").grid(
            row=4,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Combobox(
            data,
            textvariable=self.setting_vars["max_storage_mb"],
            values=(0, 100, 250, 500, 1000, 2000),
            state="readonly",
            width=10,
        ).grid(row=4, column=1, sticky="w", padx=(12, 0))

        remote_outer, remote = self._labeled_section(self.advanced_container, "NOTIFICATION DETAILS")
        remote_outer.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        remote.columnconfigure(1, weight=1)
        remote_fields = (
            ("ntfy server", "ntfy_server_url"),
            ("ntfy topic", "ntfy_topic"),
            ("ntfy priority", "ntfy_priority"),
            ("ntfy tags", "ntfy_tags"),
            ("Pushover sound", "phone_sound"),
            ("Release repository", "update_repo"),
        )
        for row, (label, key) in enumerate(remote_fields):
            ttk.Label(remote, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(remote, textvariable=self.setting_vars[key], width=38).grid(
                row=row, column=1, sticky="ew", padx=(12, 18), pady=4
            )
        ttk.Checkbutton(
            remote,
            text="Attach screenshot to ntfy",
            variable=self.setting_vars["ntfy_include_attachment"],
            **bootstyle("round-toggle"),
        ).grid(row=0, column=2, sticky="w", pady=4)
        ttk.Checkbutton(
            remote,
            text="Attach screenshot to Pushover",
            variable=self.setting_vars["phone_include_attachment"],
            **bootstyle("round-toggle"),
        ).grid(row=1, column=2, sticky="w", pady=4)
        ttk.Button(
            remote,
            text="Open Config",
            command=lambda: self.open_path(config_dir() / "config.json"),
            style="Utility.TButton",
        ).grid(row=5, column=2, sticky="e")

        belt_outer, belt = self._labeled_section(self.advanced_container, "BELT DEVELOPER TOOLS")
        belt_outer.grid(row=4, column=0, sticky="ew", pady=(0, 16))
        belt.columnconfigure(0, weight=1)
        belt_controls = ttk.Frame(belt)
        belt_controls.grid(row=0, column=0, sticky="ew")
        belt_controls.columnconfigure(0, weight=1)
        belt_toggles = ttk.Frame(belt_controls)
        belt_toggles.grid(row=0, column=0, sticky="nw")
        belt_actions = ttk.Frame(belt_controls)
        belt_actions.grid(row=0, column=1, sticky="ne", padx=(20, 0))
        self.belt_dev_mode_check = ttk.Checkbutton(
            belt_toggles,
            text="Developer logging",
            variable=self.setting_vars["belt_dev_mode"],
            command=self._schedule_auto_save,
            **bootstyle("round-toggle"),
        )
        self.belt_dev_mode_check.grid(row=0, column=0, sticky="w", pady=4)
        ttk.Button(
            belt_actions,
            text="Open Logs",
            command=lambda: self.open_path(belt_dev_dir()),
            style="Utility.TButton",
            width=16,
        ).grid(row=0, column=0, sticky="ew", pady=4)
        self.belt_template_collection_check = ttk.Checkbutton(
            belt_toggles,
            text="Save detections for review",
            variable=self.setting_vars["belt_template_collection_enabled"],
            command=self._belt_template_collection_changed,
            **bootstyle("round-toggle"),
        )
        self.belt_template_collection_check.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(
            belt_actions,
            text="Open Samples",
            command=lambda: self.open_path(belt_template_samples_dir()),
            style="Utility.TButton",
            width=16,
        ).grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Label(
            belt,
            textvariable=self.belt_samples_var,
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.root.bind_all("<MouseWheel>", self._on_options_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_options_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_options_mousewheel, add="+")

    def _highlight_active_tab(self, _event=None) -> None:
        try:
            selected = self.root.nametowidget(self.notebook.select())
        except Exception:
            return
        for button, tab in self.tab_buttons:
            button.configure(
                style="SidebarActive.TButton" if tab is selected else "Sidebar.TButton"
            )
        self.page_title_var.set(self.page_metadata.get(selected, "Droid Alerts"))

    def _on_belt_tab_opened(self, _event=None) -> None:
        try:
            selected = self.root.nametowidget(self.notebook.select())
        except Exception:
            return
        if selected is self.belt_tab:
            self._show_belt_cpu_warning_if_needed()
            self._belt_overlay_requested = True
            self._configure_belt_overlay()

    def _show_belt_cpu_warning_if_needed(self) -> None:
        config = getattr(self, "config", None) or load_config()
        if config.belt_cpu_warning_confirmed:
            return
        confirmed = self._setup_dialog(
            "Belt Tracker CPU Usage",
            intro="The belt tracker uses more CPU power than the normal chat alerts do.",
            ok_text="Confirm",
            cancel_text="",
        )
        if confirmed is None:
            return
        config = load_config()
        config.belt_cpu_warning_confirmed = True
        save_config(config)
        self.config = config

    def _wire_macos_repaint_workaround(self) -> None:
        # Tk's Aqua backend defers repainting remapped widgets, so freshly
        # selected notebook tabs keep showing stale content for up to a
        # second. Forcing a recomposite after every tab change fixes it.
        if self.root.tk.call("tk", "windowingsystem") != "aqua":
            return
        self.notebook.bind("<<NotebookTabChanged>>", self._on_macos_tab_changed, add="+")

    def _on_macos_tab_changed(self, _event=None) -> None:
        self._force_macos_repaint()
        if self._macos_repaint_after_id is not None:
            try:
                self.root.after_cancel(self._macos_repaint_after_id)
            except Exception:
                pass
        self._macos_repaint_after_id = self.root.after(60, self._macos_repaint_second_pass)

    def _macos_repaint_second_pass(self) -> None:
        self._macos_repaint_after_id = None
        self._force_macos_repaint()

    def _force_macos_repaint(self) -> None:
        try:
            self.root.update_idletasks()
            selected = self.root.nametowidget(self.notebook.select())
        except Exception:
            return
        # Canvas-hosted pages need an explicit nudge on top of the window-level
        # repaint or Aqua can leave their ttk children blank after a theme or
        # page change.
        if selected is self.settings_tab and self.options_canvas is not None:
            try:
                self.options_canvas.configure(background=self.options_canvas.cget("background"))
                self.options_canvas.yview_scroll(0, "units")
            except Exception:
                pass
            self._mark_widget_tree_damaged(selected)
        elif selected in self.scrollable_pages:
            canvas, _scrollbar, _window = self.scrollable_pages[selected]
            try:
                canvas.configure(background=canvas.cget("background"))
                canvas.yview_scroll(0, "units")
            except Exception:
                pass
            self._mark_widget_tree_damaged(selected)
        try:
            alpha = float(self.root.attributes("-alpha"))
            self.root.attributes("-alpha", max(0.0, alpha - 0.01))
            self.root.attributes("-alpha", alpha)
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def _mark_widget_tree_damaged(self, widget) -> None:
        try:
            widget.event_generate("<Expose>", when="now")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._mark_widget_tree_damaged(child)

    def on_debug_mode_toggle(self) -> None:
        debug_enabled = bool(self._value("save_debug_screenshots"))
        if not debug_enabled:
            self._set_var("share_debug_detections", False)
        self._apply_debug_share_visibility(debug_enabled)
        self._schedule_auto_save()

    def _apply_belt_tab_visibility(self, _debug_enabled: bool = True) -> None:
        """Keep Belt Tracker discoverable; only its developer tools are gated."""

        if self.belt_tab_button is None:
            return
        try:
            self.belt_tab_button.grid()
        except Exception:
            pass
        self._highlight_active_tab()

    def _apply_debug_share_visibility(self, debug_enabled: bool) -> None:
        if self.share_debug_detections_check is None:
            return
        if debug_enabled:
            self.share_debug_detections_check.grid()
        else:
            self.share_debug_detections_check.grid_remove()

    def on_droid_timers_toggle(self) -> None:
        enabled = bool(self._value("droid_timers_enabled"))
        if enabled:
            self.show_droid_timers()
        else:
            self._set_var("timer_reminders_enabled", False)
            self.hide_droid_timers()
        self._apply_timer_reminder_visibility(enabled)

    def _apply_timer_reminder_visibility(self, timers_enabled: bool) -> None:
        if self.timer_reminders_check is None:
            return
        if timers_enabled:
            self.timer_reminders_check.grid()
        else:
            self.timer_reminders_check.grid_remove()

    def show_droid_timers(self) -> None:
        if self.droid_timers is not None and self.droid_timers.alive:
            return
        from .timers import DroidTimersOverlay

        config = load_config()
        try:
            self.droid_timers = DroidTimersOverlay(
                self.root,
                scale=config.droid_timers_scale,
                center_x_ratio=config.droid_timers_center_x,
                top_y_ratio=config.droid_timers_top_y,
                on_layout_change=self._save_droid_timers_layout,
                monitor=self._current_monitor_info(),
                reminders_enabled=bool(self._value("timer_reminders_enabled")),
                reminder_seconds=config.timer_reminder_seconds,
                offset_seconds=config.timer_offset_seconds,
                on_reminder=self._on_timer_reminder,
            )
        except Exception as exc:
            print(f"[TIMERS] Failed to show Droid Timers overlay: {exc}")
            self.droid_timers = None

    def _save_droid_timers_layout(self, center_x: float, top_y: float, scale: float) -> None:
        config = load_config()
        config.droid_timers_center_x = round(center_x, 4)
        config.droid_timers_top_y = round(top_y, 4)
        config.droid_timers_scale = round(scale, 2)
        save_config(config)
        self.config = config
        self.detail_var.set("Droid Timers position and size saved")

    def adjust_droid_timers(self) -> None:
        """Show the overlay (enabling it if needed) and let the user drag it
        around / resize it; Done on the strip saves the layout."""
        if not bool(self._value("droid_timers_enabled")):
            self._set_var("droid_timers_enabled", True)
            self._apply_timer_reminder_visibility(True)
        self.show_droid_timers()
        if self.droid_timers is not None:
            self.droid_timers.enter_edit_mode()
            self.detail_var.set(
                "Drag the timer strip to move it; use − / + to resize; click Done to save"
            )

    def hide_droid_timers(self) -> None:
        if self.droid_timers is not None:
            self.droid_timers.close()
            self.droid_timers = None

    def on_advanced_toggle(self) -> None:
        advanced = bool(self._value("advanced_mode"))
        self._apply_advanced_visibility(advanced)
        self._schedule_auto_save()

    def _update_options_scrollregion(self, _event=None) -> None:
        if self.options_canvas is None or self._options_scrollregion_after_id is not None:
            return
        try:
            self._options_scrollregion_after_id = self.root.after_idle(
                self._apply_options_scrollregion
            )
        except tk.TclError:
            self._options_scrollregion_after_id = None

    def _apply_options_scrollregion(self) -> None:
        self._options_scrollregion_after_id = None
        if self.options_canvas is None:
            return
        try:
            bounds = self.options_canvas.bbox("all")
            if bounds is None or bounds == self._options_scrollregion_bounds:
                return
            self._options_scrollregion_bounds = bounds
            self.options_canvas.configure(scrollregion=bounds)
        except tk.TclError:
            return

    def _resize_options_content(self, event) -> None:
        if self.options_canvas is None or self.options_canvas_window is None:
            return
        width = max(1, int(event.width))
        if width == self._options_content_width:
            return
        self._options_content_width = width
        try:
            self.options_canvas.itemconfigure(self.options_canvas_window, width=width)
        except tk.TclError:
            self._options_content_width = None

    def _pointer_is_over_options(self) -> bool:
        if self.options_outer is None:
            return False
        try:
            widget = self.root.winfo_containing(
                self.root.winfo_pointerx(), self.root.winfo_pointery()
            )
        except Exception:
            return False
        while widget is not None:
            if widget is self.options_outer:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_options_mousewheel(self, event):
        if (
            self.options_canvas is None
            or not bool(self._value("advanced_mode"))
            or not self._pointer_is_over_options()
        ):
            return None
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = int(getattr(event, "delta", 0))
            if not delta:
                return None
            units = -int(delta / 120)
            if units == 0:
                units = -1 if delta > 0 else 1
        self.options_canvas.yview_scroll(units, "units")
        return "break"

    def _set_options_scrolling(self, enabled: bool) -> None:
        if self.options_scrollbar is None or self.options_canvas is None:
            return
        if enabled:
            self.options_scrollbar.grid()
        else:
            self.options_scrollbar.grid_remove()
            self.options_canvas.yview_moveto(0)
        self._options_scrollregion_bounds = None
        self._update_options_scrollregion()

    def _apply_advanced_visibility(self, advanced: bool) -> None:
        for widget in self.advanced_widgets:
            if advanced:
                widget.grid()
            else:
                widget.grid_remove()
        self._set_options_scrolling(advanced)

    def load_settings(self) -> None:
        self._loading_settings = True
        try:
            self.config = load_config()
            self._set_var("ui_theme", theme_label(self.config.ui_theme))
            if self.current_theme.key != normalize_theme_key(self.config.ui_theme):
                self._apply_theme(self.config.ui_theme, announce=False)
            selected = set(self.config.targets)
            for combo, var in self.alert_vars.items():
                var.set(combo in selected)
            for key in (
                "popup_enabled",
                "sound_enabled",
                "droid_timers_enabled",
                "save_alert_samples",
                "ntfy_enabled",
                "discord_enabled",
                "phone_alerts_enabled",
                "ntfy_include_attachment",
                "phone_include_attachment",
                "update_check_enabled",
                "extra_checks",
                "save_debug_screenshots",
                "share_debug_detections",
                "start_watcher_on_launch",
                "timer_reminders_enabled",
                "belt_overlay_enabled",
                "belt_dev_mode",
                "belt_template_collection_enabled",
            ):
                var = self.setting_vars.get(key)
                if isinstance(var, BooleanVar):
                    var.set(bool(getattr(self.config, key)))
            self.belt_samples_var.set(
                "Template collection is ready; start Belt Tracker to collect"
                if self.config.belt_template_collection_enabled
                else "Template collection is off"
            )
            self._set_var("monitor_index", self.config.monitor_index)
            self.refresh_monitor_choices()
            self._set_var("capture_interval_seconds", self.config.capture_interval_seconds)
            self._set_var("dedupe_seconds", self.config.dedupe_seconds)
            self._set_var("alert_cooldown_seconds", self.config.alert_cooldown_seconds)
            self._set_var(
                "validation_failures_before_calibration_prompt",
                self.config.validation_failures_before_calibration_prompt,
            )
            self._set_var("popup_seconds", self.config.popup_seconds)
            self._set_var("popup_position", self.config.popup_position.replace("_", " ").capitalize())
            self._set_var("popup_scale", self.config.popup_scale)
            self._set_var("popup_opacity", self.config.popup_opacity)
            self._set_var("sound_file", self.config.sound_file)
            self._set_var("ntfy_server_url", self.config.ntfy_server_url)
            self._set_var("ntfy_topic", self.config.ntfy_topic)
            self._set_var("ntfy_priority", self.config.ntfy_priority)
            self._set_var("ntfy_tags", self.config.ntfy_tags)
            self._set_var("phone_sound", self.config.phone_sound)
            self._set_var("update_repo", self.config.update_repo)
            self._set_var("retention_days", self.config.retention_days)
            self._set_var("max_storage_mb", self.config.max_storage_mb)
            self._set_var("timer_reminder_seconds", self.config.timer_reminder_seconds)
            self._set_var("timer_offset_seconds", self.config.timer_offset_seconds)
            self._set_var("belt_idle_scan_fps", self.config.belt_idle_scan_fps)
            self._set_var("belt_active_scan_fps", self.config.belt_active_scan_fps)
            timers_enabled = bool(self.config.droid_timers_enabled)
            if not timers_enabled:
                self._set_var("timer_reminders_enabled", False)
            self._apply_timer_reminder_visibility(timers_enabled)
            if not self.config.save_debug_screenshots:
                self._set_var("share_debug_detections", False)
            self._set_var("advanced_mode", self.config.advanced_mode)
            self._apply_debug_share_visibility(self.config.save_debug_screenshots)
            self._apply_belt_tab_visibility()
            self._apply_advanced_visibility(self.config.advanced_mode)
            self.refresh_sound_choices()
            self.refresh_channel_statuses()
            self._refresh_belt_target_text()
            self._load_belt_region()
            self._refresh_capture_source_text()
            if not self.is_watching():
                self.watcher_detail_var.set(self._watcher_ready_text())
            self.detail_var.set("Settings loaded")
        finally:
            self._loading_settings = False

    def _capture_target_label(self, config: AppConfig | None = None) -> str:
        config = config or self.config
        if config.capture_source == "window":
            selected = (
                config.capture_window_title
                or config.capture_window_process
                or "Selected window"
            )
            return f"Window: {selected}"
        if config.capture_source == "device":
            return f"Capture device: {config.capture_device_name or 'Selected device'}"
        return self.monitor_display_var.get() or f"Monitor {config.monitor_index}"

    def _watcher_ready_text(self) -> str:
        if self.config.capture_source == "window":
            return f"{self._capture_target_label()} is selected. Start watching when Fortnite is open."
        if self.config.capture_source == "device":
            return f"{self._capture_target_label()} is selected. Start watching when the console is visible."
        return "Choose the display with Fortnite, or select its window or capture device, then start watching."

    def _refresh_capture_source_text(self) -> None:
        if not hasattr(self, "capture_source_var"):
            return
        self.capture_source_var.set(f"Both watchers: {self._capture_target_label()}")

    def _set_monitor_capture_source(self) -> None:
        self.config.capture_source = "monitor"
        self.config.capture_window_title = ""
        self.config.capture_window_process = ""
        self.config.capture_window_class = ""
        self.config.capture_device_name = ""
        self.config.capture_device_path = ""
        self.config.capture_device_vid = None
        self.config.capture_device_pid = None
        self.config.capture_device_backend = 0
        self._refresh_capture_source_text()

    @staticmethod
    def _device_selector(config: AppConfig) -> dict[str, object]:
        return {
            "name": config.capture_device_name,
            "path": config.capture_device_path,
            "vid": config.capture_device_vid,
            "pid": config.capture_device_pid,
            "preferred_backend": config.capture_device_backend,
            "monitor_index": config.monitor_index,
        }

    def _ensure_device_capture_session(self, config: AppConfig) -> DeviceCaptureSession:
        session = getattr(self, "device_capture_session", None)
        selector = self._device_selector(config)
        if session is not None and session.matches(**selector):
            return session
        if session is not None:
            session.close()
        session = session_from_config(
            config,
            context=multiprocessing.get_context("spawn"),
        )
        self.device_capture_session = session
        try:
            session.screen_size()
        except Exception:
            self.device_capture_session = None
            session.close()
            raise
        return session

    def _maybe_close_device_capture_session(self, *, force: bool = False) -> None:
        session = getattr(self, "device_capture_session", None)
        if session is None:
            return
        if not force and self.config.capture_source == "device" and (
            self.is_watching() or self.is_belt_tracking()
        ):
            return
        self.device_capture_session = None
        session.close()

    def _create_runtime_capture(self, config: AppConfig):
        if config.capture_source == "device":
            session = getattr(self, "device_capture_session", None)
            if session is None or not session.matches(**self._device_selector(config)):
                raise RuntimeError("The selected capture device session is not running.")
            return session.client()
        return create_capture(
            monitor_index=config.monitor_index,
            capture_source=config.capture_source,
            window_title=config.capture_window_title,
            window_process=config.capture_window_process,
            window_class=config.capture_window_class,
        )

    def _create_chat_capture(self, config: AppConfig | None = None):
        config = config or self.config
        if config.capture_source == "device":
            session = getattr(self, "device_capture_session", None)
            if session is not None and session.matches(**self._device_selector(config)):
                return session.client()
        return create_capture(
            monitor_index=config.monitor_index,
            capture_source=config.capture_source,
            window_title=config.capture_window_title,
            window_process=config.capture_window_process,
            window_class=config.capture_window_class,
            device_name=config.capture_device_name,
            device_path=config.capture_device_path,
            device_vid=config.capture_device_vid,
            device_pid=config.capture_device_pid,
            device_backend=config.capture_device_backend,
        )

    @staticmethod
    def _capture_area(capture):
        return getattr(capture, "capture_area", getattr(capture, "monitor", None))

    def _current_capture_key(self) -> str | None:
        config = self.config
        if config.capture_source == "window":
            return window_capture_key(
                title=config.capture_window_title,
                process_name=config.capture_window_process,
                class_name=config.capture_window_class,
            )
        if config.capture_source == "device":
            return device_capture_key(
                name=config.capture_device_name,
                path=config.capture_device_path,
                vid=config.capture_device_vid,
                pid=config.capture_device_pid,
            )
        return getattr(self._current_monitor_info(), "key", None)

    def refresh_monitor_choices(self, *, sync_belt: bool = True) -> None:
        selected_index = max(1, int(self._value("monitor_index")))
        try:
            monitors = list_monitors()
        except Exception as exc:
            print(f"[GUI] Failed to list monitors: {exc}")
            monitors = []

        if not monitors:
            fallback_label = f"Monitor {selected_index}"
            self.monitor_indexes_by_label = {fallback_label: selected_index}
            self.monitor_combobox.configure(values=(fallback_label,))
            self.monitor_display_var.set(fallback_label)
            self._refresh_capture_source_text()
            return

        primary = next((monitor for monitor in monitors if monitor.is_primary), monitors[0])
        labels = [format_monitor_label(monitor, primary) for monitor in monitors]
        self.monitor_indexes_by_label = {
            label: monitor.index for label, monitor in zip(labels, monitors)
        }
        self.monitor_combobox.configure(values=labels)

        selected_monitor = next(
            (monitor for monitor in monitors if monitor.index == selected_index),
            monitors[0],
        )
        if selected_monitor.index != selected_index:
            self._set_var("monitor_index", selected_monitor.index)
        self.monitor_display_var.set(format_monitor_label(selected_monitor, primary))
        self._refresh_capture_source_text()
        if sync_belt and selected_monitor.index != selected_index:
            self._on_belt_monitor_changed()

    def _apply_monitor_index(self, monitor_index: int) -> None:
        previous_index = max(1, int(self._value("monitor_index")))
        self._set_var("monitor_index", max(1, int(monitor_index)))
        self.refresh_monitor_choices(sync_belt=False)
        current_index = max(1, int(self._value("monitor_index")))
        if current_index != previous_index:
            self._on_belt_monitor_changed()

    def _on_belt_monitor_changed(self) -> None:
        if self.is_belt_tracking():
            self._belt_restart_after_stop = True
            self.stop_belt_tracking(reason="monitor-change")
        self._load_belt_region()
        self.belt_last_scan_var.set("No belt scans yet")

    def on_monitor_selected(self, _event=None) -> None:
        label = self.monitor_display_var.get()
        monitor_index = self.monitor_indexes_by_label.get(label)
        if monitor_index is None:
            return
        previous_index = max(1, int(self._value("monitor_index")))
        overlay_was_open = False
        overlay_error = ""
        if monitor_index != previous_index and self.region_overlay is not None:
            try:
                overlay_was_open = bool(self.region_overlay.winfo_exists())
            except Exception:
                overlay_was_open = False
            if overlay_was_open:
                self.close_region_overlay()
        self._apply_monitor_index(monitor_index)
        self._set_monitor_capture_source()
        saved = self.save_settings(interactive=False, update_detail=False)
        if overlay_was_open and saved is not None:
            try:
                self.toggle_region_overlay()
            except Exception as exc:
                overlay_error = str(exc)
        if self.droid_timers is not None:
            self.hide_droid_timers()
            self.show_droid_timers()
        if overlay_error:
            self.detail_var.set(f"{label} selected; chat region could not be shown: {overlay_error}")
        else:
            self.detail_var.set(f"{label} selected and applied")

    def select_capture_window(self) -> None:
        if sys.platform != "win32":
            self._show_message(
                "Select Window",
                "Window capture is available in the Windows app.",
            )
            return

        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title("Select Window")
        dialog.transient(self.root)
        dialog.minsize(560, 420)
        dialog.geometry("620x500")

        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        ttk.Label(body, text="Select Window", font=self._font(14, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            body,
            text=WINDOW_CAPTURE_EXPLANATION,
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            body,
            text=(
                "Keep Fortnite restored; Windows can pause capture while it is minimized. "
                "This changes both the chat watcher and Belt Tracker. Timers still use Game display."
            ),
            wraplength=560,
            justify="left",
            **muted_style(),
        ).grid(row=2, column=0, sticky="w", pady=(5, 14))

        feedback_var = StringVar(value="Open Fortnite before refreshing this list.")
        ttk.Label(
            body,
            textvariable=feedback_var,
            wraplength=560,
            justify="left",
            **muted_style(),
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        list_frame = ttk.Frame(body)
        list_frame.grid(row=4, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        picker = ttk.Treeview(
            list_frame,
            columns=("window", "application"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        picker.heading("window", text="Window")
        picker.heading("application", text="Application")
        picker.column("window", anchor="w", stretch=True, minwidth=280)
        picker.column("application", anchor="w", stretch=False, width=150)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=picker.yview)
        picker.configure(yscrollcommand=scrollbar.set)
        picker.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        buttons.columnconfigure(2, weight=1)

        windows_by_item: dict[str, WindowDescriptor] = {}
        select_button = None

        def selected_window() -> WindowDescriptor | None:
            selection = picker.selection()
            if not selection:
                return None
            return windows_by_item.get(selection[0])

        def refresh_windows() -> None:
            nonlocal windows_by_item
            for item in picker.get_children():
                picker.delete(item)
            windows_by_item = {}
            try:
                windows = list_capture_windows()
            except Exception as exc:
                feedback_var.set(f"Windows could not be listed: {exc}")
                if select_button is not None:
                    select_button.configure(state="disabled")
                return

            current = self.config
            current_item = ""
            fortnite_item = ""
            for index, window in enumerate(windows):
                item = f"window-{index}"
                windows_by_item[item] = window
                picker.insert(
                    "",
                    "end",
                    iid=item,
                    values=(window.title, window.process_name or "Unknown application"),
                )
                if not current_item and current.capture_source == "window":
                    process_matches = (
                        current.capture_window_process
                        and window.process_name.casefold()
                        == current.capture_window_process.casefold()
                    )
                    title_matches = (
                        current.capture_window_title
                        and window.title.casefold() == current.capture_window_title.casefold()
                    )
                    if process_matches or title_matches:
                        current_item = item
                if not fortnite_item and window.is_fortnite:
                    fortnite_item = item

            if not windows_by_item:
                feedback_var.set("No selectable windows were found. Open Fortnite, then refresh.")
                if select_button is not None:
                    select_button.configure(state="disabled")
                return

            preferred_item = current_item or fortnite_item or next(iter(windows_by_item))
            picker.selection_set(preferred_item)
            picker.focus(preferred_item)
            picker.see(preferred_item)
            feedback_var.set(
                f"{len(windows_by_item)} window{'s' if len(windows_by_item) != 1 else ''} found."
            )
            if select_button is not None:
                select_button.configure(state="normal")

        def close_dialog() -> None:
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        def use_game_display() -> None:
            overlay_was_open = False
            if self.region_overlay is not None:
                try:
                    overlay_was_open = bool(self.region_overlay.winfo_exists())
                except Exception:
                    overlay_was_open = False
            if overlay_was_open:
                self.close_region_overlay()
            if self.is_belt_tracking():
                self._belt_restart_after_stop = True
                self.stop_belt_tracking(reason="monitor-change")
            self._set_monitor_capture_source()
            saved = self.save_settings(interactive=False, update_detail=False)
            if saved is None:
                feedback_var.set("Fix the invalid setting shown in the main window, then try again.")
                if overlay_was_open:
                    try:
                        self.toggle_region_overlay()
                    except Exception:
                        pass
                return
            self.watcher_detail_var.set(self._watcher_ready_text())
            self.detail_var.set("Chat watcher changed back to Game display")
            close_dialog()
            if overlay_was_open:
                self.root.after(0, self.toggle_region_overlay)

        def apply_window() -> None:
            window = selected_window()
            if window is None:
                feedback_var.set("Select the Fortnite window first.")
                return

            feedback_var.set(f'Checking capture for "{window.title}"…')
            dialog.update_idletasks()
            test_capture = None
            try:
                test_capture = create_capture(
                    monitor_index=max(1, int(self._value("monitor_index"))),
                    capture_source="window",
                    window_title=window.title,
                    window_process=window.process_name,
                    window_class=window.class_name,
                )
                test_capture.screen_size()
            except Exception as exc:
                feedback_var.set(f"That window could not be captured: {exc}")
                return
            finally:
                if test_capture is not None:
                    try:
                        test_capture.close()
                    except Exception:
                        pass

            overlay_was_open = False
            if self.region_overlay is not None:
                try:
                    overlay_was_open = bool(self.region_overlay.winfo_exists())
                except Exception:
                    overlay_was_open = False
            if overlay_was_open:
                self.close_region_overlay()

            if self.is_belt_tracking():
                self._belt_restart_after_stop = True
                self.stop_belt_tracking(reason="monitor-change")
            self.config.capture_source = "window"
            self.config.capture_window_title = window.title
            self.config.capture_window_process = window.process_name
            self.config.capture_window_class = window.class_name
            self.config.capture_device_name = ""
            self.config.capture_device_path = ""
            self.config.capture_device_vid = None
            self.config.capture_device_pid = None
            self.config.capture_device_backend = 0
            saved = self.save_settings(interactive=False, update_detail=False)
            if saved is None:
                feedback_var.set("Fix the invalid setting shown in the main window, then try again.")
                if overlay_was_open:
                    try:
                        self.toggle_region_overlay()
                    except Exception:
                        pass
                return

            self._refresh_capture_source_text()
            self.watcher_detail_var.set(self._watcher_ready_text())
            self.detail_var.set(f'Chat watcher set to "{window.title}"')
            close_dialog()
            if overlay_was_open:
                def reopen_overlay() -> None:
                    try:
                        self.toggle_region_overlay()
                    except Exception as exc:
                        self.detail_var.set(
                            f'Window selected; chat region could not be shown: {exc}'
                        )

                self.root.after(0, reopen_overlay)

        ttk.Button(
            buttons,
            text="Refresh",
            command=refresh_windows,
            style="Utility.TButton",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            buttons,
            text="Use Game Display",
            command=use_game_display,
            style="Utility.TButton",
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            buttons,
            text="Cancel",
            command=close_dialog,
            **bootstyle("secondary"),
        ).grid(row=0, column=3, padx=(0, 8))
        select_button = ttk.Button(
            buttons,
            text="Select Window",
            command=apply_window,
            **bootstyle("primary"),
        )
        select_button.grid(row=0, column=4)

        picker.bind("<Double-1>", lambda _event: apply_window())
        dialog.bind("<Return>", lambda _event: apply_window())
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        refresh_windows()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        try:
            monitors = list_monitors()
        except Exception:
            monitors = []
        x, y = clamp_dialog_position(
            x,
            y,
            dialog.winfo_width(),
            dialog.winfo_height(),
            monitors,
        )
        dialog.geometry(format_tk_geometry(x=x, y=y))
        dialog.grab_set()
        picker.focus_set()
        dialog.wait_window()

    def select_capture_device(self) -> None:
        if sys.platform != "win32":
            self._show_message(
                "Select Capture Device",
                "Direct capture-device support is available in the Windows app.",
            )
            return

        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title("Select Capture Device")
        dialog.transient(self.root)
        dialog.minsize(560, 400)
        dialog.geometry("620x480")

        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)
        ttk.Label(body, text="Select Capture Device", font=self._font(14, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            body,
            text="Choose the capture card receiving your console video. This changes both watchers.",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            body,
            text="If a device is busy, close OBS or the capture card's preview application and refresh.",
            wraplength=560,
            justify="left",
            **muted_style(),
        ).grid(row=2, column=0, sticky="w", pady=(5, 14))
        feedback_var = StringVar(value="Connect the capture card, then refresh the list.")
        ttk.Label(body, textvariable=feedback_var, wraplength=560, **muted_style()).grid(
            row=3, column=0, sticky="w", pady=(0, 8)
        )

        picker = ttk.Treeview(
            body,
            columns=("device", "backend"),
            show="headings",
            selectmode="browse",
            height=11,
        )
        picker.heading("device", text="Video capture device")
        picker.heading("backend", text="Windows backend")
        picker.column("device", anchor="w", stretch=True, minwidth=300)
        picker.column("backend", anchor="w", stretch=False, width=150)
        picker.grid(row=4, column=0, sticky="nsew")

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        buttons.columnconfigure(2, weight=1)
        devices_by_item: dict[str, CaptureDeviceDescriptor] = {}
        select_button = None

        def selected_device() -> CaptureDeviceDescriptor | None:
            selection = picker.selection()
            return devices_by_item.get(selection[0]) if selection else None

        def refresh_devices() -> None:
            nonlocal devices_by_item
            for item in picker.get_children():
                picker.delete(item)
            devices_by_item = {}
            try:
                devices = list_capture_devices()
            except Exception as exc:
                feedback_var.set(f"Capture devices could not be listed: {exc}")
                if select_button is not None:
                    select_button.configure(state="disabled")
                return
            current_item = ""
            for index, device in enumerate(devices):
                item = f"device-{index}"
                devices_by_item[item] = device
                picker.insert("", "end", iid=item, values=(device.name, device.backend_name))
                if self.config.capture_source == "device" and (
                    (self.config.capture_device_path and device.path == self.config.capture_device_path)
                    or (
                        not self.config.capture_device_path
                        and device.name.casefold() == self.config.capture_device_name.casefold()
                    )
                ):
                    current_item = item
            if not devices_by_item:
                feedback_var.set("No video capture devices were found.")
                if select_button is not None:
                    select_button.configure(state="disabled")
                return
            preferred = current_item or next(iter(devices_by_item))
            picker.selection_set(preferred)
            picker.focus(preferred)
            feedback_var.set(
                f"{len(devices_by_item)} video device{'s' if len(devices_by_item) != 1 else ''} found."
            )
            if select_button is not None:
                select_button.configure(state="normal")

        def close_dialog() -> None:
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        def apply_device() -> None:
            device = selected_device()
            if device is None:
                feedback_var.set("Select a capture device first.")
                return
            feedback_var.set(f'Checking video from "{device.name}"…')
            dialog.update_idletasks()
            candidate = AppConfig.from_dict(self.config.to_dict())
            candidate.capture_source = "device"
            candidate.capture_window_title = ""
            candidate.capture_window_process = ""
            candidate.capture_window_class = ""
            candidate.capture_device_name = device.name
            candidate.capture_device_path = device.path
            candidate.capture_device_vid = device.vid
            candidate.capture_device_pid = device.pid
            candidate.capture_device_backend = device.backend

            test_session = None
            try:
                test_session = session_from_config(
                    candidate,
                    context=multiprocessing.get_context("spawn"),
                )
                width, height = test_session.screen_size()
            except Exception as exc:
                feedback_var.set(str(exc))
                return
            finally:
                if test_session is not None:
                    test_session.close()

            if self.is_watching():
                try:
                    self._ensure_device_capture_session(candidate)
                except Exception as exc:
                    feedback_var.set(str(exc))
                    return
            if self.is_belt_tracking():
                self._belt_restart_after_stop = True
                self.stop_belt_tracking(reason="monitor-change")
            self.config = candidate
            saved = self.save_settings(interactive=False, update_detail=False)
            if saved is None:
                feedback_var.set("Fix the invalid setting shown in the main window, then try again.")
                return
            self._load_belt_region()
            self._refresh_capture_source_text()
            self.watcher_detail_var.set(self._watcher_ready_text())
            self.detail_var.set(f'Capture device set to "{device.name}" ({width} × {height})')
            close_dialog()

        ttk.Button(
            buttons, text="Refresh", command=refresh_devices, style="Utility.TButton"
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="Cancel", command=close_dialog, **bootstyle("secondary")).grid(
            row=0, column=3, padx=(0, 8)
        )
        select_button = ttk.Button(
            buttons, text="Use Capture Device", command=apply_device, **bootstyle("primary")
        )
        select_button.grid(row=0, column=4)
        picker.bind("<Double-1>", lambda _event: apply_device())
        dialog.bind("<Return>", lambda _event: apply_device())
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        refresh_devices()
        dialog.grab_set()
        picker.focus_set()

    def _current_monitor_info(self) -> MonitorInfo | None:
        index = max(1, int(self._value("monitor_index")))
        try:
            descriptor = next((item for item in list_monitors() if item.index == index), None)
        except Exception:
            descriptor = None
        if descriptor is None:
            return None
        return MonitorInfo(
            left=descriptor.left,
            top=descriptor.top,
            width=descriptor.width,
            height=descriptor.height,
            index=descriptor.index,
            key=descriptor.key,
            name=descriptor.name,
        )

    def _load_belt_region(self) -> None:
        monitor = self._current_belt_source_info()
        self.belt_overlay.close()
        relative = load_belt_region(monitor) if monitor is not None else None
        self.belt_region = relative.to_pixels(monitor) if relative is not None and monitor is not None else None
        self._refresh_belt_region_text()
        self._configure_belt_overlay()

    def _current_belt_source_info(self, *, open_device: bool = False) -> MonitorInfo | None:
        monitor = self._current_monitor_info()
        if monitor is None or self.config.capture_source != "device":
            return monitor
        session = getattr(self, "device_capture_session", None)
        capture = None
        try:
            if session is not None and session.matches(**self._device_selector(self.config)):
                capture = session.client()
            elif open_device:
                capture = self._create_chat_capture(self.config)
            if capture is not None:
                width, height = capture.screen_size()
            else:
                width, height = 1920, 1080
        finally:
            if capture is not None:
                capture.close()
        return MonitorInfo(
            left=monitor.left,
            top=monitor.top,
            width=width,
            height=height,
            index=monitor.index,
            key=self._current_capture_key() or "device:unknown",
            name=self.config.capture_device_name or "Capture device",
        )

    def _refresh_belt_region_text(self) -> None:
        if self.belt_region is None:
            self.belt_region_var.set("No belt region selected for this display")
            return
        region = self.belt_region
        self.belt_region_var.set(
            f"Region: {region.left}, {region.top} · {region.width} × {region.height}"
        )

    def _refresh_belt_target_text(self) -> None:
        target_tiers = normalize_belt_target_tiers(self.config.belt_target_tiers)
        names = [name for name in BELT_DROID_NAMES if name in target_tiers]
        self.belt_targets_var.set(
            f"{len(names)} alert rule{'s' if len(names) != 1 else ''}"
            if names
            else "No alert rules"
        )
        if self.belt_priority_tree is None:
            return
        for item in self.belt_priority_tree.get_children():
            self.belt_priority_tree.delete(item)
        for name in names:
            self.belt_priority_tree.insert(
                "",
                "end",
                values=(name, belt_target_label(target_tiers[name])),
            )

    def select_belt_region(self) -> None:
        if self.is_belt_tracking():
            self.belt_detail_var.set("Stop Belt Tracker before changing its region.")
            return
        source = self._current_belt_source_info(open_device=True)
        display_monitor = self._current_monitor_info()
        if source is None or display_monitor is None:
            self._show_message(
                "Belt Region",
                "The selected Dashboard display is not available.",
                tone="danger",
            )
            return
        if not self._confirm_belt_region_guide_if_needed():
            return
        self._belt_overlay_requested = True
        self.belt_overlay.close()
        try:
            self._belt_selector_root_state = str(self.root.state())
            self.root.iconify()
            self.root.update_idletasks()
            # Windows needs a compositor cycle after iconify() or the capture
            # can still contain the main Droid Alerts window.
            self.root.after(
                300,
                lambda source=source, display_monitor=display_monitor: self._open_belt_region_selector(
                    source, display_monitor
                ),
            )
        except Exception as exc:
            self._restore_after_belt_selection()
            self._show_message("Belt Region", str(exc), tone="danger")

    def _confirm_belt_region_guide_if_needed(self) -> bool:
        config = getattr(self, "config", None) or load_config()
        if config.belt_region_guide_confirmed:
            return True
        confirmed = self._setup_dialog(
            "Official Belt Tracker Setup",
            intro=(
                "This is the recommended and only officially supported Belt Tracker setup. "
                "Stand at the start of the belt and match the cyan box as closely as possible, "
                "with two complete blueprint cards visible. Price labels may be inside the box; "
                "Belt Tracker ignores them."
            ),
            image_path=assets_dir() / "belt_region_guide.png",
            note="Other camera angles, distances, and framing may not detect reliably.",
            ok_text="Use This Setup",
            cancel_text="",
        )
        if confirmed is None:
            return False
        config = load_config()
        config.belt_region_guide_confirmed = True
        save_config(config)
        self.config = config
        return True

    def _open_belt_region_selector(
        self,
        monitor: MonitorInfo,
        display_monitor: MonitorInfo | None = None,
    ) -> None:
        try:
            capture = self._create_chat_capture() if self.config.capture_source == "device" else None
            self.belt_selector = BeltRegionSelector(
                self.root,
                monitor,
                lambda box, monitor=monitor: self._belt_region_selected(box, monitor),
                on_cancelled=self._belt_region_cancelled,
                capture=capture,
                display_monitor=display_monitor,
            )
        except Exception as exc:
            self._restore_after_belt_selection()
            self._show_message("Belt Region", str(exc), tone="danger")

    def _restore_after_belt_selection(self) -> None:
        previous_state = self._belt_selector_root_state
        self._belt_selector_root_state = None
        try:
            self.root.deiconify()
            if previous_state == "zoomed":
                self.root.state("zoomed")
            self.root.lift()
        except (RuntimeError, tk.TclError):
            pass

    def _belt_region_cancelled(self) -> None:
        self.belt_selector = None
        self._restore_after_belt_selection()
        self._configure_belt_overlay()

    def _belt_region_selected(self, box: PixelBox, monitor: MonitorInfo) -> None:
        self.belt_selector = None
        self._restore_after_belt_selection()
        save_belt_region(monitor, BeltRelativeRegion.from_pixels(box, monitor))
        self.belt_status_var.set("Belt region saved")
        current = self._current_monitor_info()
        if current is not None and current.key == monitor.key:
            self.belt_region = box
            self._refresh_belt_region_text()
            self.belt_detail_var.set("Ready to track the selected blueprint belt region.")
        else:
            self._load_belt_region()
            self.belt_detail_var.set(
                "The region was saved for its original display; Dashboard now uses another display."
            )
        self._configure_belt_overlay()

    def choose_belt_targets(self) -> None:
        if self.is_belt_tracking():
            self.belt_detail_var.set("Stop Belt Tracker before changing target droids.")
            return

        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title("Priority Alerts")
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Priority Alerts", font=self._font(14, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Choose the minimum belt tier for each droid. Higher tiers also alert: "
                "Default → Gold → Diamond → Rainbow → Beskar."
            ),
            wraplength=650,
            justify="left",
            **muted_style(),
        ).pack(anchor="w", pady=(4, 12))

        search_row = ttk.Frame(body)
        search_row.pack(fill="x", pady=(0, 10))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 8))
        search_var = StringVar()
        search_entry = ttk.Entry(search_row, textvariable=search_var)
        search_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(
            search_row,
            text="Clear",
            command=lambda: search_var.set(""),
            style="Utility.TButton",
        ).grid(row=0, column=2, padx=(8, 0))

        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True)
        picker = ttk.Treeview(
            list_frame,
            columns=("droid", "minimum_tier"),
            show="headings",
            selectmode="extended",
            height=9,
        )
        picker.heading("droid", text="Droid")
        picker.heading("minimum_tier", text="Alert from")
        picker.column("droid", anchor="w", stretch=True, minwidth=220)
        picker.column("minimum_tier", anchor="w", stretch=False, width=130)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=picker.yview)
        picker.configure(yscrollcommand=scrollbar.set)
        picker.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        rules = normalize_belt_target_tiers(self.config.belt_target_tiers)
        count_var = StringVar()
        feedback_var = StringVar()
        tier_var = StringVar(value=BELT_TARGET_LABELS[BELT_FAMILY_ORDER[0]])
        tier_values = ("Off",) + tuple(
            BELT_TARGET_LABELS[family] for family in BELT_FAMILY_ORDER
        )

        def refresh_count(_event=None) -> None:
            selected_count = len(picker.selection())
            visible_count = len(picker.get_children())
            count_var.set(
                f"{len(rules)} alert rule{'s' if len(rules) != 1 else ''} · "
                f"{visible_count} shown · {selected_count} selected"
            )

        def refresh_picker(*_args) -> None:
            selected = set(picker.selection())
            query = search_var.get().strip().casefold()
            for item in picker.get_children():
                picker.delete(item)
            for name in BELT_DROID_NAMES:
                if query and query not in name.casefold():
                    continue
                picker.insert(
                    "",
                    "end",
                    iid=name,
                    values=(name, belt_target_label(rules.get(name))),
                )
                if name in selected:
                    picker.selection_add(name)
            refresh_count()

        def select_all(_event=None) -> str:
            picker.selection_set(picker.get_children())
            refresh_count()
            return "break"

        def apply_tier(_event=None) -> str:
            selected = tuple(picker.selection())
            if not selected:
                feedback_var.set("Select one or more droids first.")
                return "break"
            label = tier_var.get()
            family = BELT_TARGET_FAMILIES_BY_LABEL.get(label)
            for name in selected:
                if family is None:
                    rules.pop(name, None)
                else:
                    rules[name] = family
                if picker.exists(name):
                    picker.item(name, values=(name, belt_target_label(rules.get(name))))
            feedback_var.set(
                f"Updated {len(selected)} droid{'s' if len(selected) != 1 else ''} to {label}."
            )
            refresh_count()
            return "break"

        def cycle_tier(event) -> str:
            name = picker.identify_row(event.y)
            if not name:
                return "break"
            current = rules.get(name)
            choices: tuple[str | None, ...] = (None,) + BELT_FAMILY_ORDER
            next_family = choices[(choices.index(current) + 1) % len(choices)]
            if next_family is None:
                rules.pop(name, None)
            else:
                rules[name] = next_family
            picker.selection_set(name)
            picker.item(name, values=(name, belt_target_label(rules.get(name))))
            tier_var.set(belt_target_label(rules.get(name)))
            feedback_var.set(f"{name}: {belt_target_label(rules.get(name))}")
            refresh_count()
            return "break"

        def set_all_default() -> None:
            rules.clear()
            rules.update({name: BELT_FAMILY_ORDER[0] for name in BELT_DROID_NAMES})
            feedback_var.set("Every droid will alert from Default upward.")
            refresh_picker()

        def clear_rules() -> None:
            rules.clear()
            feedback_var.set("All belt alert rules cleared.")
            refresh_picker()

        def save_targets(_event=None) -> str:
            config = load_config()
            config.belt_target_tiers = normalize_belt_target_tiers(rules)
            save_config(config)
            self.config = config
            self._refresh_belt_target_text()
            self.belt_status_var.set("Belt alert rules saved")
            dialog.destroy()
            return "break"

        picker.bind("<<TreeviewSelect>>", refresh_count)
        picker.bind("<Control-a>", select_all)
        picker.bind("<Command-a>", select_all)
        picker.bind("<Double-1>", cycle_tier)
        search_var.trace_add("write", refresh_picker)
        ttk.Label(body, textvariable=count_var, **muted_style()).pack(anchor="w", pady=(8, 0))

        edit_row = ttk.Frame(body)
        edit_row.pack(fill="x", pady=(10, 0))
        ttk.Label(edit_row, text="Set selected to").pack(side="left")
        tier_picker = ttk.Combobox(
            edit_row,
            textvariable=tier_var,
            values=tier_values,
            state="readonly",
            width=14,
        )
        tier_picker.pack(side="left", padx=(8, 0))
        tier_picker.bind("<<ComboboxSelected>>", apply_tier)
        ttk.Button(
            edit_row,
            text="Apply",
            command=apply_tier,
            **bootstyle("info-outline"),
        ).pack(side="left", padx=(8, 0))
        ttk.Label(edit_row, textvariable=feedback_var, **muted_style()).pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(
            body,
            text="Tip: select multiple rows to edit together, or double-click a row to advance it.",
            **muted_style(),
        ).pack(anchor="w", pady=(6, 0))

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            actions,
            text="All Default+",
            command=set_all_default,
            **bootstyle("info-outline"),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Clear all",
            command=clear_rules,
            **bootstyle("danger-outline"),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(actions, text="Save", command=save_targets, **bootstyle("primary")).pack(
            side="right"
        )
        dialog.bind("<Control-s>", save_targets)
        dialog.bind("<Command-s>", save_targets)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        refresh_picker()
        dialog.update_idletasks()
        dialog_width, dialog_height = fit_window_size(
            max(PRIORITY_DIALOG_WIDTH, dialog.winfo_reqwidth() + 20),
            max(PRIORITY_DIALOG_HEIGHT, dialog.winfo_reqheight() + 20),
            dialog.winfo_screenwidth(),
            dialog.winfo_screenheight(),
            horizontal_margin=80,
            vertical_margin=120,
        )
        dialog.geometry(f"{dialog_width}x{dialog_height}")
        dialog.minsize(min(620, dialog_width), min(700, dialog_height))
        search_entry.focus_set()

    def _belt_overlay_changed(self) -> None:
        if bool(self._value("belt_overlay_enabled")):
            self._belt_overlay_requested = True
            self._configure_belt_overlay()
        else:
            self.belt_overlay.close()

    def _belt_template_collection_changed(self) -> None:
        enabled = bool(self._value("belt_template_collection_enabled"))
        self.belt_samples_var.set(
            "Template collection is ready; start Belt Tracker to collect"
            if enabled
            else "Template collection is off"
        )
        self._schedule_auto_save()

    def _configure_belt_overlay(self) -> None:
        self.belt_overlay.close()
        if (
            not getattr(self, "_belt_overlay_requested", False)
            or not bool(self._value("belt_overlay_enabled"))
            or self.belt_region is None
        ):
            return
        monitor = self._current_monitor_info()
        if monitor is None:
            return
        try:
            region = self.belt_region
            self._belt_overlay_scale = (1.0, 1.0)
            if self.config.capture_source == "device":
                source = self._current_belt_source_info()
                if source is not None:
                    scale_x = monitor.width / max(1, source.width)
                    scale_y = monitor.height / max(1, source.height)
                    self._belt_overlay_scale = (scale_x, scale_y)
                    region = PixelBox(
                        round(region.left * scale_x),
                        round(region.top * scale_y),
                        max(1, round(region.width * scale_x)),
                        max(1, round(region.height * scale_y)),
                    )
            self.belt_overlay.configure(monitor, region)
            self.belt_overlay.update_tracks(self._scaled_belt_overlay_tracks())
        except Exception as exc:
            self.belt_overlay.close()
            self.belt_detail_var.set(f"Belt overlay could not open: {exc}")

    def _scaled_belt_overlay_tracks(self) -> list[dict[str, object]]:
        scale_x, scale_y = self._belt_overlay_scale
        if (scale_x, scale_y) == (1.0, 1.0):
            return self._belt_visible_tracks
        result: list[dict[str, object]] = []
        for track in self._belt_visible_tracks:
            item = dict(track)
            box = tuple(int(value) for value in track.get("box", (0, 0, 0, 0)))
            if len(box) == 4:
                item["box"] = (
                    round(box[0] * scale_x),
                    round(box[1] * scale_y),
                    max(1, round(box[2] * scale_x)),
                    max(1, round(box[3] * scale_y)),
                )
            result.append(item)
        return result

    def identify_displays(self) -> None:
        try:
            monitors = list_monitors()
        except Exception as exc:
            self._show_message(
                "Identify Displays",
                f"Displays could not be read:\n{exc}",
                tone="danger",
            )
            return
        if not monitors:
            self._show_message("Identify Displays", "No displays were found.")
            return
        overlays: list[tk.Toplevel] = []
        for monitor in monitors:
            window = tk.Toplevel(self.root)
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            try:
                window.attributes("-alpha", 0.92)
            except Exception:
                pass
            width = min(420, max(280, monitor.width // 3))
            height = 150
            x = monitor.left + max(0, (monitor.width - width) // 2)
            y = monitor.top + max(0, (monitor.height - height) // 2)
            window.geometry(format_tk_geometry(width=width, height=height, x=x, y=y))
            window.configure(bg="#111827")
            tk.Label(
                window,
                text=f"MONITOR {monitor.index}",
                bg="#111827",
                fg="#57d8ff",
                font=self._font(28, "bold"),
            ).pack(pady=(22, 2))
            tk.Label(
                window,
                text=f"{monitor.width} × {monitor.height}" + ("  ·  Primary" if monitor.is_primary else ""),
                bg="#111827",
                fg="white",
                font=self._font(13),
            ).pack()
            overlays.append(window)
        self.detail_var.set("Display numbers are shown on each screen for 3 seconds")
        self.root.after(3000, lambda: [window.destroy() for window in overlays if window.winfo_exists()])

    def refresh_sound_choices(self) -> None:
        if not hasattr(self, "sound_combobox"):
            return
        from .config import sounds_dir

        names: list[str] = []
        for folder in (user_sounds_dir(), sounds_dir()):
            if folder.exists():
                names.extend(path.name for path in folder.glob("*.wav") if path.is_file())
        values = ("System beeps", *sorted(set(names), key=str.casefold))
        self.sound_combobox.configure(values=values)
        current = str(self._value("sound_file"))
        if not current or current not in values:
            self._set_var("sound_file", "System beeps" if not names else names[0])

    def add_alert_sound(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="Add alert sound",
            filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
        )
        if not source:
            return
        source_path = Path(source)
        if source_path.suffix.lower() != ".wav":
            self._show_message(
                "Alert Sound",
                "Droid Alerts currently supports WAV files.",
                tone="danger",
            )
            return
        try:
            folder = user_sounds_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source_path.name
            if source_path.resolve() != target.resolve():
                shutil.copy2(source_path, target)
        except OSError as exc:
            self._show_message("Alert Sound", str(exc), tone="danger")
            return
        self.refresh_sound_choices()
        self._set_var("sound_file", target.name)
        self.detail_var.set(f"Alert sound added: {target.name}")

    def _update_dashboard_timers(self) -> None:
        offset = int(getattr(self.config, "timer_offset_seconds", 0))
        for kind, var in self.timer_vars.items():
            var.set(format_countdown(seconds_until_next(kind, offset)))
        monitoring = self.session_monitoring_seconds
        if self._watch_segment_started is not None:
            monitoring += time.monotonic() - self._watch_segment_started
        minutes = int(monitoring) // 60
        self.session_stats_var.set(
            f"{self.session_detection_count} detections · {self.session_alert_count} alerts · "
            f"{minutes // 60:02d}:{minutes % 60:02d} watching"
        )
        self._dashboard_timer_after_id = self.root.after(500, self._update_dashboard_timers)

    def _on_timer_reminder(self, kind: str, remaining: int) -> None:
        try:
            self.root.bell()
        except Exception:
            pass

    def show_privacy_details(self) -> None:
        self._setup_dialog(
            "Privacy Details",
            intro="Detection runs locally from pixels on the selected display.",
            steps=(
                "While the app is open, Droid Alerts sends a small anonymous heartbeat using random install and session IDs so combined open time can be measured.",
                "The chat watcher has its own heartbeat and shares which priority alert options are selected when those options change.",
                "Priority chat alerts share the app version and detected droid/rarity. Belt Tracker uses its own heartbeat while running and periodically shares only confirmed droid names and compact counts.",
                "Automatic telemetry never shares raw Belt Tracker frames, chat text, player or machine names, credentials, or screenshots.",
                "Your install stays anonymous unless you use Identify This Install to voluntarily link it to your Discord account and a username. That identity is visible only to the developer.",
                "Screenshots stay on this PC unless a notification attachment or the separate debug-sharing option is explicitly enabled.",
                "Support bundles redact notification topics and never include webhook or API credentials.",
            ),
            ok_text="Close",
            cancel_text="",
        )

    def show_install_identity(self) -> None:
        install_id = load_or_create_anonymous_install_id()
        result = self._setup_dialog(
            "Identify This Install",
            intro=(
                "By default, your information is anonymous and only you know your install ID. "
                "If you want to make it known to the developer:"
            ),
            steps=(
                "Copy the install ID below.",
                "Open gonk.tools/identify and log in with Discord.",
                "Paste the install ID, enter your username, and save it.",
            ),
            fields=(("install_id", "Your install ID", install_id, None),),
            note="This is optional. Your identity is shown only to the developer, not on the public stats page.",
            link=("Open Identification Page", IDENTIFY_INSTALL_URL),
            ok_text="Copy Install ID",
            cancel_text="Close",
        )
        if result is None:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(install_id)
            self.root.update_idletasks()
        except tk.TclError:
            self.detail_var.set("Could not copy the install ID. Select it in the dialog instead.")
            return
        self.detail_var.set("Install ID copied. Open gonk.tools/identify to identify this install.")

    def show_faq(self) -> None:
        if sys.platform == "win32":
            clock_help = (
                "Timers out of sync? Open Windows Settings → Time & language → Date & time. "
                "Turn on Set time automatically, then press Sync now. The timers update immediately."
            )
        elif sys.platform == "darwin":
            clock_help = (
                "Timers out of sync? Open System Settings → General → Date & Time, then turn on "
                "Set time and date automatically. The timers update immediately."
            )
        else:
            clock_help = (
                "Timers out of sync? Enable automatic date and time in your system settings. "
                "The timers update immediately when the system clock changes."
            )
        self._setup_dialog(
            "FAQ",
            steps=(
                clock_help,
                "Still slightly out of sync? Enable Advanced settings and adjust Timer schedule offset. "
                "Use a negative value when the timers are early and a positive value when they are late.",
                "No detections? Check Game display on the Dashboard, then use Show Chat Region in "
                "Diagnostics to confirm the box covers Fortnite's chat alerts.",
                "No alert sound? Make sure Sound is enabled, choose System beeps or a WAV file, then "
                "use Test All Alerts on the Dashboard.",
                "Need help? Create a Support Bundle in Diagnostics. It includes logs and settings but "
                "removes notification credentials.",
            ),
            ok_text="Close",
            cancel_text="",
        )

    def show_belt_faq(self) -> None:
        self._setup_dialog(
            "Belt Tracker Guide",
            steps=(
                "Use the recommended and only officially supported setup shown during the first "
                "belt selection: stand at the start of the belt and keep the same camera angle.",
                "Click Select Belt Region, then click and drag to match the cyan example box with "
                "two complete blueprint cards visible. Price labels may be inside the box; they are ignored.",
                "Press Enter to save the selected region.",
                "Enable Show belt overlay and confirm the box matches the official example. Other "
                "angles, distances, and framing are not officially supported.",
                "For detector review, keep two complete blueprints visible, enable "
                "Save detections for review before starting, and leave tracking running. It keeps "
                "one best complete crop per appearance and at most 20 diverse crops per detected "
                "droid without slowing or changing the template detector.",
                "Advanced Settings has separate Belt idle and active scan FPS controls. They "
                "default to 4 and 8, accept 1 to 20, and higher rates use more CPU.",
                "For troubleshooting, enable Dev mode before starting Belt Tracker. Reproduce "
                "the issue for about a minute, stop tracking, then create a Support Bundle in Diagnostics.",
            ),
            ok_text="Close",
            cancel_text="",
        )

    def is_watching(self) -> bool:
        return self.watch_thread is not None and self.watch_thread.is_alive()

    def toggle_watcher(self) -> None:
        if self.is_watching():
            self.stop_watcher()
        else:
            self.start_watcher()

    def is_belt_tracking(self) -> bool:
        # Keep the lifecycle busy until the UI poller has reaped a stopped
        # process, so a fast second click cannot overwrite its queue/state.
        return self.belt_process is not None

    def toggle_belt_tracking(self) -> None:
        if self.is_belt_tracking():
            self.stop_belt_tracking()
        else:
            self.start_belt_tracking()

    def start_belt_tracking(self) -> None:
        if self._shutting_down:
            return
        if self.is_belt_tracking():
            self.belt_detail_var.set("Belt Tracker is already running.")
            return
        monitor = self._current_monitor_info()
        if monitor is None:
            self.belt_status_var.set("Display unavailable")
            self.belt_detail_var.set("Choose an available game display from Dashboard.")
            return
        if not self._confirm_belt_region_guide_if_needed():
            return
        if self.config.capture_source == "device":
            try:
                self._ensure_device_capture_session(self.config)
            except Exception as exc:
                self._set_belt_header_state("Error")
                self.belt_status_var.set("Capture device unavailable")
                self.belt_detail_var.set(str(exc))
                return
        belt_source = self._current_belt_source_info()
        relative = load_belt_region(belt_source) if belt_source is not None else None
        self.belt_region = (
            relative.to_pixels(belt_source)
            if relative is not None and belt_source is not None
            else None
        )
        self._refresh_belt_region_text()
        if self.belt_region is None:
            self.belt_status_var.set("Select the belt region first")
            self.belt_detail_var.set(BELT_REGION_INSTRUCTIONS)
            return

        config = load_config()
        config.monitor_index = monitor.index
        config.belt_overlay_enabled = bool(self._value("belt_overlay_enabled"))
        config.belt_dev_mode = bool(self._value("belt_dev_mode"))
        config.belt_template_collection_enabled = bool(
            self._value("belt_template_collection_enabled")
        )
        save_config(config)
        self.config = config

        device_spec = None
        if config.capture_source == "device":
            try:
                device_spec = self._ensure_device_capture_session(config).spec
            except Exception as exc:
                self._set_belt_header_state("Error")
                self.belt_status_var.set("Capture device unavailable")
                self.belt_detail_var.set(str(exc))
                return

        context = multiprocessing.get_context("spawn")
        self.belt_stop_event = context.Event()
        self.belt_status_queue = context.Queue()
        self._belt_stop_reason = ""
        self._belt_error_message = ""
        self._belt_worker_ready = False
        self._belt_visible_tracks = []
        self.belt_telemetry = AnonymousBeltTelemetryClient(config)
        self.belt_last_scan_var.set("Waiting for first belt scan…")
        process = context.Process(
            target=run_belt_worker_process,
            args=(
                monitor.index,
                self.belt_region,
                dict(config.belt_target_tiers),
                self.belt_stop_event,
                self.belt_status_queue,
                config.belt_dev_mode,
                config.belt_template_collection_enabled,
                config.belt_idle_scan_fps,
                config.belt_active_scan_fps,
                config.capture_source,
                config.capture_window_title,
                config.capture_window_process,
                config.capture_window_class,
                config.capture_device_name,
                config.capture_device_path,
                config.capture_device_vid,
                config.capture_device_pid,
                config.capture_device_backend,
                device_spec,
            ),
            name="DroidAlertsBeltTracker",
            daemon=True,
        )
        self.belt_process = process
        try:
            process.start()
        except Exception as exc:
            self._belt_worker_finished(exc, process)
            return
        self._belt_poll_after_id = self.root.after(50, self._poll_belt_process)
        self._set_belt_header_state("Running")
        self._set_belt_loading_state()
        self._set_belt_controls(running=True)
        self._belt_overlay_requested = True
        self._configure_belt_overlay()
        self.detail_var.set("Belt Tracker started")

    def _set_belt_loading_state(self) -> None:
        self.belt_status_var.set("Loading Belt Tracker")
        self.belt_detail_var.set("This can take a little bit")

    def _drain_belt_status_queue(self) -> None:
        status_queue = self.belt_status_queue
        if status_queue is None:
            return
        while True:
            try:
                event = status_queue.get_nowait()
            except QueueEmpty:
                return
            except (EOFError, OSError, ValueError):
                return
            if isinstance(event, dict):
                self._handle_belt_status(event)

    def _poll_belt_process(self) -> None:
        self._belt_poll_after_id = None
        process = self.belt_process
        if process is None:
            return
        self._drain_belt_status_queue()
        if process is not self.belt_process:
            return
        if process.is_alive():
            self._belt_poll_after_id = self.root.after(50, self._poll_belt_process)
            return
        process.join(timeout=0)
        self._drain_belt_status_queue()
        exit_code = process.exitcode
        error = None
        if exit_code not in (None, 0):
            error = RuntimeError(f"Belt Tracker process exited with code {exit_code}")
        self._belt_worker_finished(error, process)

    def _handle_belt_status(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "ready":
            self._belt_worker_ready = True
            if self.belt_telemetry is not None:
                self.belt_telemetry.start()
            self._belt_error_message = ""
            self._set_belt_header_state("Running")
            self.belt_status_var.set("Tracking blueprint belt")
            if self.belt_region is not None:
                self.belt_detail_var.set(
                    f"{self.monitor_display_var.get()} · Region "
                    f"{self.belt_region.width} × {self.belt_region.height}"
                )
        elif event_type == "scan":
            self._belt_error_message = ""
            self._set_belt_header_state("Running")
            accepted = int(event.get("accepted_count") or 0)
            candidates = int(event.get("candidate_count") or 0)
            detector = str(event.get("detector") or "templates")
            scan_fps = float(event.get("scan_fps") or 0.0)
            track_timeout = float(event.get("track_timeout_seconds") or 0.0)
            self.belt_status_var.set("Tracking blueprint belt")
            detector_label = "template"
            candidate_label = "visual candidates"
            scan_text = (
                f"Latest scan: {accepted} accepted · {candidates} {candidate_label}"
                f" · {scan_fps:.1f} {detector_label} FPS"
            )
            if bool(self._value("belt_dev_mode")) and track_timeout > 0:
                scan_text += f" · {track_timeout:.1f}s track timeout"
            self.belt_last_scan_var.set(scan_text)
        elif event_type == "tracks":
            tracks = event.get("tracks")
            if isinstance(tracks, list):
                self._belt_visible_tracks = [track for track in tracks if isinstance(track, dict)]
                count = len(self._belt_visible_tracks)
                self.belt_tracks_var.set(f"{count} active track{'s' if count != 1 else ''}")
                if bool(self._value("belt_overlay_enabled")):
                    self.belt_overlay.update_tracks(self._scaled_belt_overlay_tracks())
        elif event_type == "track_event":
            record = event.get("record")
            if isinstance(record, dict):
                self.refresh_logs(update_detail=False)
                if str(record.get("event") or "") == "entered":
                    telemetry = self.belt_telemetry
                    if telemetry is not None:
                        telemetry.record_sighting(record.get("droid"))
                if bool(record.get("alerted")):
                    self._send_belt_alert(record)
        elif event_type == "sample_collection":
            error = str(event.get("error") or "").strip()
            if error:
                self.belt_samples_var.set(error)
            elif bool(event.get("enabled")):
                total = int(event.get("total_samples") or 0)
                droid_count = int(event.get("droid_count") or 0)
                maximum = int(event.get("max_per_droid") or 20)
                action = str(event.get("action") or "")
                name = str(event.get("name") or "")
                per_droid = int(event.get("samples_for_droid") or 0)
                if action in {"saved", "replaced"} and name:
                    verb = "saved" if action == "saved" else "upgraded"
                    self.belt_samples_var.set(
                        f"{name} {verb} ({per_droid}/{maximum}) · "
                        f"{total} samples across {droid_count} droids"
                    )
                else:
                    self.belt_samples_var.set(
                        f"Collecting · {total} samples across {droid_count} droids · "
                        f"max {maximum} each"
                    )
        elif event_type == "error":
            message = str(event.get("message") or "Unknown Belt Tracker error")
            self._belt_error_message = message
            self._set_belt_header_state("Warning")
            self.belt_status_var.set("Belt Tracker warning")
            self.belt_detail_var.set(message)
        elif event_type == "capture_error":
            message = str(event.get("message") or "The capture source is unavailable.")
            self._set_belt_header_state("Warning")
            self.belt_status_var.set("Waiting for capture source")
            self.belt_detail_var.set(message)
        elif event_type == "capture_reconnected":
            self._belt_error_message = ""
            self._set_belt_header_state("Running")
            self.belt_status_var.set("Tracking blueprint belt")
            self.belt_detail_var.set("Capture source reconnected automatically.")
        elif event_type == "dev_log":
            path = str(event.get("path") or "belt_dev")
            self.detail_var.set(f"Belt dev log: data/{path}")

    def _send_belt_alert(self, record: dict[str, object]) -> None:
        droid = str(record.get("droid") or "").strip()
        if not droid:
            return
        config = load_config()
        family = str(record.get("card_family") or "").strip()
        if not family:
            possible_family = str(record.get("rarity") or "").strip().split(" ", 1)[0]
            if possible_family in BELT_FAMILY_ORDER:
                family = possible_family
        if not is_belt_alert_target(config.belt_target_tiers, droid, family):
            return
        try:
            confidence = min(1.0, max(0.0, float(record.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        rarity = str(record.get("rarity") or "").strip() or "Belt"
        try:
            rarity_confidence = min(
                1.0,
                max(0.0, float(record.get("rarity_confidence") or 0.0)),
            )
        except (TypeError, ValueError):
            rarity_confidence = 0.0
        detection = Detection(
            droid=droid,
            rarity=rarity,
            row_box=(0, 0, 0, 0),
            droid_score=confidence,
            rarity_score=rarity_confidence,
            rarity_margin=rarity_confidence,
            score=confidence,
            source="belt-tracker",
            shape_score=1.0,
        )

        if config.sound_enabled:
            try:
                AlertPolicy(config).notify(detection)
            except Exception as exc:
                self.channel_status_vars["Sound"].set("Failed to play")
                self.detail_var.set(f"Alert sound failed: {exc}")
        if config.popup_enabled:
            show_popup(
                detection,
                config.popup_seconds,
                icon_path=popup_icon_path(config),
                parent=self.root,
                monitor=self._current_monitor_info(),
                position=config.popup_position,
                scale=config.popup_scale,
                opacity=config.popup_opacity,
            )

        deliveries: list[tuple[str, object, tuple[object, ...], dict[str, object]]] = []
        if config.discord_enabled:
            try:
                webhook_url, _source = load_discord_webhook(config)
            except Exception as exc:
                webhook_url = None
                self.channel_status_vars["Discord"].set(f"Failed · {str(exc)[:70]}")
            if webhook_url:
                deliveries.append(("Discord", send_discord_alert, (webhook_url, detection), {}))
        if config.ntfy_enabled and ntfy_configured(config):
            deliveries.append(
                ("ntfy", send_ntfy_alert, (config, detection), {"attachment_path": None})
            )
        if config.phone_alerts_enabled:
            try:
                credentials, _source = load_phone_alert_credentials(config)
            except Exception as exc:
                credentials = None
                self.channel_status_vars["Pushover"].set(f"Failed · {str(exc)[:70]}")
            if credentials:
                deliveries.append(
                    (
                        "Pushover",
                        send_phone_alert,
                        (credentials, detection),
                        {"sound": config.phone_sound, "attachment_path": None},
                    )
                )

        for label, target, args, kwargs in deliveries:
            self.channel_status_vars[label].set("Sending…")
            threading.Thread(
                target=self._deliver_belt_alert,
                args=(label, target, args, kwargs, detection),
                name=f"DroidAlertsBelt{label}",
                daemon=True,
            ).start()

    def _deliver_belt_alert(
        self,
        label: str,
        target,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        detection: Detection,
    ) -> None:
        try:
            result = target(*args, **kwargs)
            success = bool(getattr(result, "success", False))
            detail = str(getattr(result, "message", "") or "")
        except Exception as exc:
            success = False
            detail = str(exc)
        delivery_event: dict[str, object] = {
            "ts": timestamp(),
            "event_type": "delivery",
            "source": "belt_tracker",
            "channel": label,
            "success": success,
            "detail": detail,
            "droid": detection.droid,
            "rarity": "" if detection.rarity == "Belt" else detection.rarity,
            "alerted": True,
            "is_priority": True,
            "score": detection.score,
        }
        append_event(delivery_event)
        self._post_to_ui(
            lambda label=label, event=delivery_event: self._belt_delivery_finished(label, event)
        )

    def _belt_delivery_finished(self, label: str, event: dict[str, object]) -> None:
        success = bool(event.get("success"))
        detail = str(event.get("detail") or "")
        self.channel_status_vars[label].set(
            "Delivered just now" if success else f"Failed · {detail[:70]}"
        )
        self.refresh_logs(update_detail=False)

    def _set_belt_controls(self, *, running: bool) -> None:
        if running:
            self.belt_watch_button.configure(
                text="Stop Tracking", state="normal", **bootstyle("danger")
            )
            self.belt_region_button.configure(state="disabled")
            self.belt_targets_button.configure(state="disabled")
            if self.belt_dev_mode_check is not None:
                self.belt_dev_mode_check.configure(state="disabled")
            if self.belt_template_collection_check is not None:
                self.belt_template_collection_check.configure(state="disabled")
        else:
            self.belt_watch_button.configure(
                text="Start Tracking", state="normal", **bootstyle("success")
            )
            self.belt_region_button.configure(state="normal")
            self.belt_targets_button.configure(state="normal")
            if self.belt_dev_mode_check is not None:
                self.belt_dev_mode_check.configure(state="normal")
            if self.belt_template_collection_check is not None:
                self.belt_template_collection_check.configure(state="normal")

    def _belt_worker_finished(
        self,
        exc: Exception | None,
        process=None,
    ) -> None:
        if process is not None and self.belt_process is not process:
            return
        reason = self._belt_stop_reason
        restart = (
            self._belt_restart_after_stop
            and reason == "monitor-change"
            and not self._shutting_down
        )
        self._belt_restart_after_stop = False
        telemetry = getattr(self, "belt_telemetry", None)
        self.belt_telemetry = None
        if telemetry is not None:
            telemetry.stop()
        self.belt_process = None
        self.belt_stop_event = None
        status_queue = self.belt_status_queue
        self.belt_status_queue = None
        self._belt_worker_ready = False
        self._belt_stop_reason = ""
        self._belt_visible_tracks = []
        self.belt_tracks_var.set("0 active tracks")
        if status_queue is not None:
            try:
                status_queue.close()
            except (OSError, ValueError):
                pass
        if process is not None:
            try:
                process.close()
            except (OSError, ValueError):
                pass
        self._set_belt_controls(running=False)

        error_message = str(exc) if exc is not None else self._belt_error_message
        self._belt_error_message = ""
        if restart:
            if self.config.capture_source != "device":
                self._maybe_close_device_capture_session()
            self._load_belt_region()
            if self.belt_region is not None:
                self._set_belt_header_state("Running")
                self.belt_status_var.set("Restarting Belt Tracker…")
                self.root.after(100, self.start_belt_tracking)
            else:
                self._set_belt_header_state("Stopped")
                self.belt_status_var.set("Select the belt region first")
                self.belt_detail_var.set(
                    "The new Dashboard display needs its own belt region."
                )
            return
        if error_message and reason not in {"manual", "close", "update"}:
            self._set_belt_header_state("Error")
            self.belt_status_var.set("Belt Tracker stopped")
            self.belt_detail_var.set(error_message)
            self._show_message("Belt Tracker", error_message, tone="danger")
        else:
            self._set_belt_header_state("Stopped")
            self.belt_status_var.set("Ready to track")
            self.belt_detail_var.set(
                "Ready to track the selected blueprint belt region."
                if self.belt_region is not None
                else BELT_REGION_INSTRUCTIONS
            )
        if not self._shutting_down:
            self._configure_belt_overlay()
        self._maybe_close_device_capture_session()
        self.detail_var.set("Belt Tracker stopped")

    def stop_belt_tracking(self, *, reason: str = "manual") -> None:
        if self.belt_stop_event is None:
            self.detail_var.set("Belt Tracker is not running")
            return
        self._belt_stop_reason = reason
        self.belt_stop_event.set()
        process = self.belt_process
        if not self._belt_worker_ready and process is not None and process.is_alive():
            # Recognizer construction does not observe the event until it returns.
            # It is safe to terminate because recognition is isolated here.
            process.terminate()
        self.belt_watch_button.configure(text="Stopping…", state="disabled")
        self.belt_status_var.set("Stopping Belt Tracker…")

    def _terminate_belt_process(self) -> None:
        telemetry = getattr(self, "belt_telemetry", None)
        self.belt_telemetry = None
        if telemetry is not None:
            telemetry.stop()
        if self._belt_poll_after_id is not None:
            try:
                self.root.after_cancel(self._belt_poll_after_id)
            except (RuntimeError, tk.TclError):
                pass
            self._belt_poll_after_id = None
        if self.belt_stop_event is not None:
            self.belt_stop_event.set()
        process = self.belt_process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.2)
                process.close()
            except (OSError, ValueError):
                pass
        status_queue = self.belt_status_queue
        if status_queue is not None:
            try:
                status_queue.close()
            except (OSError, ValueError):
                pass
        self.belt_process = None
        self.belt_stop_event = None
        self.belt_status_queue = None
        self._belt_worker_ready = False

    def _post_to_ui(self, callback) -> None:
        # Worker threads must not touch Tk directly; after() is the marshal
        # point and raises once the main loop is gone, so drop late callbacks.
        try:
            self.root.after(0, callback)
        except (RuntimeError, tk.TclError):
            pass

    def _queue_watcher_status(self, event: dict[str, object]) -> None:
        self._post_to_ui(lambda event=event: self._handle_watcher_status(event))

    def _handle_watcher_status(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in {"watcher_ready", "config_reloaded"}:
            monitor_index = event.get("monitor_index")
            if monitor_index is not None and int(monitor_index) != int(self._value("monitor_index")):
                self._apply_monitor_index(int(monitor_index))
            width = event.get("screen_width", "?")
            height = event.get("screen_height", "?")
            source = event.get("region_source", "automatic")
            capture_source = str(event.get("capture_source") or "monitor")
            capture_label = str(event.get("capture_label") or "").strip()
            if capture_source == "window":
                target_label = f"Window: {capture_label or 'Selected window'}"
            elif capture_source == "device":
                target_label = f"Capture device: {capture_label or 'Selected device'}"
            else:
                target_label = self.monitor_display_var.get()
            self.watcher_status_var.set("Watching for priority spawns")
            self.watcher_detail_var.set(
                f"{target_label} · {width} × {height} · Region: {source}"
            )
            self._set_watcher_state("Running")
            if capture_source != "device":
                self._maybe_close_device_capture_session()
        elif event_type == "scan":
            stamp = str(event.get("scanned_at") or "")
            self.last_scan_var.set(f"Last successful scan: {self._display_timestamp(stamp)}")
        elif event_type in {"detection", "alert"}:
            row = event.get("event")
            if isinstance(row, dict):
                self.session_detection_count += 1
                if event_type == "alert":
                    self.session_alert_count += 1
                    self.last_alert_var.set(
                        f"Last alert: {row.get('rarity', '')} {row.get('droid', '')} · {self._display_timestamp(str(row.get('ts', '')))}"
                    )
        elif event_type == "delivery":
            result = event.get("result")
            if isinstance(result, dict):
                channel = str(result.get("channel") or "")
                key = "Pushover" if channel.lower() in {"phone", "pushover"} else channel
                if key in self.channel_status_vars:
                    success = bool(result.get("success"))
                    detail = str(result.get("detail") or "")
                    self.channel_status_vars[key].set("Delivered just now" if success else f"Failed · {detail[:70]}")
        elif event_type == "capture_error":
            message = str(event.get("message") or "Unknown capture error")
            self._set_watcher_state("Warning")
            self.watcher_detail_var.set(f"Screen capture failed; retrying automatically: {message}")
        elif event_type == "sound_error":
            message = str(event.get("message") or "Unknown sound error")
            self.channel_status_vars["Sound"].set(f"Failed · {message[:70]}")
            self.watcher_detail_var.set(
                "The alert continued through the other channels, but its sound could not be played."
            )
        elif event_type == "log_error":
            message = str(event.get("message") or "Unknown log error")
            self._set_watcher_state("Warning")
            self.watcher_detail_var.set(
                f"History could not be written; alert delivery is still running: {message}"
            )
        if event_type in {"alert", "detection", "delivery"}:
            self.refresh_logs(update_detail=False)

    def _set_watcher_state(self, state: str) -> None:
        self._watcher_header_state = state
        self._refresh_header_status()
        if state in {"Running", "Warning"} or self.is_watching():
            self.watch_button.configure(text="Stop Watching", state="normal", **bootstyle("danger"))
        else:
            self.watch_button.configure(text="Start Watching", state="normal", **bootstyle("success"))

    def _set_belt_header_state(self, state: str) -> None:
        self._belt_header_state = state
        self._refresh_header_status()

    def _refresh_header_status(self) -> None:
        states = (self._watcher_header_state, self._belt_header_state)
        if "Error" in states:
            state = "Error"
        elif "Warning" in states:
            state = "Warning"
        elif "Running" in states:
            state = "Running"
        elif "Paused" in states:
            state = "Paused"
        else:
            state = "Stopped"
        self.status_var.set(state)
        if hasattr(self, "sidebar_status_var"):
            self.sidebar_status_var.set(f"●  {state}")
        self._apply_watcher_status_style(state)

    def _apply_watcher_status_style(self, state: str) -> None:
        palette = getattr(self, "current_theme", theme_for(DEFAULT_THEME_KEY))
        color = {
            "Running": palette.colors["success"],
            "Paused": palette.colors["warning"],
            "Warning": palette.colors["warning"],
            "Stopped": palette.colors["danger"],
            "Error": palette.colors["danger"],
        }.get(state, palette.sidebar_muted)
        ttk.Style().configure(
            "SidebarStatus.TLabel",
            background=palette.sidebar_bg,
            foreground=color,
        )
        self.header_status_label.configure(style="SidebarStatus.TLabel")

    @staticmethod
    def _display_timestamp(value: str) -> str:
        text = value.strip()
        try:
            if len(text) >= 15 and text[8] == "_":
                return f"{text[9:11]}:{text[11:13]}:{text[13:15]}"
        except Exception:
            pass
        return text or "just now"

    def refresh_channel_statuses(self) -> None:
        config = self.config if hasattr(self, "config") else load_config()
        self.channel_status_vars["Popup"].set("Ready" if config.popup_enabled else "Off")
        self.channel_status_vars["Sound"].set("Ready" if config.sound_enabled else "Off")

        def configured(check) -> bool:
            try:
                return bool(check(config))
            except Exception:
                return False

        checks = (
            ("Discord", config.discord_enabled, configured(discord_webhook_configured)),
            ("ntfy", config.ntfy_enabled, configured(ntfy_configured)),
            ("Pushover", config.phone_alerts_enabled, configured(phone_alerts_configured)),
        )
        for label, enabled, configured in checks:
            self.channel_status_vars[label].set("Ready" if enabled and configured else ("Off" if configured else "Not configured"))

    def _start_runtime_features(self) -> None:
        self.config = load_config()
        if self.config.start_watcher_on_launch and not self.is_watching():
            self.start_watcher()

    def _set_var(self, key: str, value: object) -> None:
        var = self.setting_vars.get(key)
        if hasattr(var, "set"):
            var.set(value)

    def _wire_auto_save(self) -> None:
        for var in [*self.setting_vars.values(), *self.alert_vars.values()]:
            if hasattr(var, "trace_add"):
                var.trace_add("write", self._on_setting_changed)
        self._autosave_ready = True

    def _on_setting_changed(self, *_args) -> None:
        if self._loading_settings:
            return
        self._schedule_auto_save()

    def _schedule_auto_save(self, delay_ms: int = 600) -> None:
        if not self._autosave_ready:
            return
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.root.after(delay_ms, self._auto_save_settings)

    def _auto_save_settings(self) -> None:
        self._autosave_after_id = None
        saved = self.save_settings(interactive=False, update_detail=False)
        if saved is None:
            self.detail_var.set("Settings not saved — fix the invalid numeric value")
        else:
            self.detail_var.set("Settings saved automatically")

    def run_first_time_intro(self) -> None:
        """First-launch walkthrough for region checking, timers, and phone alerts."""
        config = load_config()
        # Existing installs (already past the phone prompt) skip the intro.
        if config.intro_shown or config.notification_setup_prompted:
            if not config.intro_shown:
                config.intro_shown = True
                save_config(config)
                self.config = config
            self.prompt_notification_setup_if_needed()
            return

        self._setup_dialog(
            "Before You Start",
            intro="Droid Alerts guesses where your Fortnite chat alerts appear. "
            "Quickly check the region box before you start farming:",
            steps=(
                "Open Fortnite and stand somewhere in-game where droid spawn messages appear.",
                'Click "Show Chat Region" in Diagnostics. A red outline will appear on your screen.',
                "That outline should cover the chat alerts, not the middle of the screen or the HUD.",
                'If it is misplaced, use the arrow buttons to move the fixed-size box until it lines up.',
            ),
            ok_text="Got It",
            cancel_text="Skip For Now",
            modal=False,
        )

        timers_choice = self._setup_dialog(
            "Droid Timers",
            intro="Do you want a small Droid Timers bar at the top of your screen?\n\n"
            "It counts down "
            "to the next Beskar, Mythic and Rainbow spawns. You can turn it on "
            "or off any time in Settings.",
            ok_text="Show Timers",
            cancel_text="No Thanks",
        )
        enable_timers = timers_choice is not None
        self._set_var("droid_timers_enabled", enable_timers)
        if enable_timers:
            self.show_droid_timers()
        else:
            self.hide_droid_timers()

        config = load_config()
        config.droid_timers_enabled = enable_timers
        config.intro_shown = True
        save_config(config)
        self.config = config

        self.prompt_notification_setup_if_needed()

    def offer_discord_community(self) -> None:
        """Offer the community link once to installs carrying a pre-1.3 config."""
        config = load_config()
        if __version__ != "1.3.0" or config.discord_community_prompted:
            return

        # Save before opening the modal/browser so either answer is one-time.
        config.discord_community_prompted = True
        save_config(config)
        self.config = config
        join = self._confirm_message(
            "Join the Droid Alerts Discord?",
            "Would you like to join the Discord for update alerts, support, and game leaks?",
            confirm_text="Join Discord",
            cancel_text="Not now",
        )
        if join:
            webbrowser.open(DISCORD_COMMUNITY_URL)

    def prompt_notification_setup_if_needed(self) -> None:
        config = load_config()
        if config.notification_setup_prompted:
            return
        if (
            (config.ntfy_enabled and ntfy_configured(config))
            or (config.discord_enabled and discord_webhook_configured(config))
            or (config.phone_alerts_enabled and phone_alerts_configured(config))
        ):
            config.notification_setup_prompted = True
            save_config(config)
            self.config = config
            return

        choice = self._setup_dialog(
            "Get Alerts On Your Phone",
            intro="Droid Alerts can ping your phone the moment a priority droid spawns, "
            "even when you're away from your PC.\n\n"
            "The easiest way is the free ntfy app. Setting it up takes about two minutes. "
            "Want to do that now?",
            note="You can always do this later with the Set Up ntfy or Set Up Pushover "
            "buttons in Settings.",
            ok_text="Set Up ntfy",
            cancel_text="Maybe Later",
        )
        if choice is not None:
            if self.setup_ntfy_alerts_and_enable():
                return

        config = load_config()
        config.notification_setup_prompted = True
        save_config(config)
        self.config = config

    def save_settings(self, *, interactive: bool = True, update_detail: bool = True) -> AppConfig | None:
        previous_config = self.config
        selected = [combo for combo, var in self.alert_vars.items() if var.get()]
        if interactive and not selected and not self._confirm_message(
            "No Priority Alerts",
            "Continue with no priority alerts selected?",
            confirm_text="Continue",
            tone="warning",
        ):
            return None

        config = load_config()
        config.capture_source = previous_config.capture_source
        config.capture_window_title = previous_config.capture_window_title
        config.capture_window_process = previous_config.capture_window_process
        config.capture_window_class = previous_config.capture_window_class
        config.capture_device_name = previous_config.capture_device_name
        config.capture_device_path = previous_config.capture_device_path
        config.capture_device_vid = previous_config.capture_device_vid
        config.capture_device_pid = previous_config.capture_device_pid
        config.capture_device_backend = previous_config.capture_device_backend
        try:
            config.monitor_index = max(1, int(self._value("monitor_index")))
            config.capture_interval_seconds = max(0.05, float(self._value("capture_interval_seconds")))
            config.dedupe_seconds = max(0.0, float(self._value("dedupe_seconds")))
            config.alert_cooldown_seconds = max(0.0, float(self._value("alert_cooldown_seconds")))
            config.validation_failures_before_calibration_prompt = max(
                1, int(self._value("validation_failures_before_calibration_prompt"))
            )
            config.popup_seconds = max(0.5, float(self._value("popup_seconds")))
            config.popup_scale = min(1.5, max(0.7, float(self._value("popup_scale"))))
            config.popup_opacity = min(1.0, max(0.55, float(self._value("popup_opacity"))))
            config.retention_days = max(0, int(self._value("retention_days")))
            config.max_storage_mb = max(0, int(self._value("max_storage_mb")))
            config.timer_reminder_seconds = max(1, int(self._value("timer_reminder_seconds")))
            config.timer_offset_seconds = max(-3600, min(3600, int(self._value("timer_offset_seconds"))))
            (
                config.belt_idle_scan_fps,
                config.belt_active_scan_fps,
            ) = normalize_belt_scan_fps(
                self._value("belt_idle_scan_fps"),
                self._value("belt_active_scan_fps"),
            )
            self._set_var("belt_idle_scan_fps", config.belt_idle_scan_fps)
            self._set_var("belt_active_scan_fps", config.belt_active_scan_fps)
        except (TypeError, ValueError, tk.TclError) as exc:
            if interactive:
                self._show_message(
                    "Settings",
                    f"Invalid numeric setting: {exc}",
                    tone="danger",
                )
            elif update_detail:
                self.detail_var.set("Settings not saved: invalid numeric value")
            return None

        config.sound_enabled = bool(self._value("sound_enabled"))
        config.popup_enabled = bool(self._value("popup_enabled"))
        config.droid_timers_enabled = bool(self._value("droid_timers_enabled"))
        config.save_alert_samples = bool(self._value("save_alert_samples"))
        config.save_debug_screenshots = bool(self._value("save_debug_screenshots"))
        config.share_debug_detections = config.save_debug_screenshots and bool(self._value("share_debug_detections"))
        config.ntfy_enabled = bool(self._value("ntfy_enabled"))
        config.discord_enabled = bool(self._value("discord_enabled"))
        config.phone_alerts_enabled = bool(self._value("phone_alerts_enabled"))
        config.ntfy_include_attachment = bool(self._value("ntfy_include_attachment"))
        config.phone_include_attachment = bool(self._value("phone_include_attachment"))
        config.update_check_enabled = bool(self._value("update_check_enabled"))
        config.extra_checks = bool(self._value("extra_checks"))
        config.start_watcher_on_launch = bool(self._value("start_watcher_on_launch"))
        config.ui_theme = normalize_theme_key(self._value("ui_theme"))
        config.belt_overlay_enabled = bool(self._value("belt_overlay_enabled"))
        config.belt_dev_mode = bool(self._value("belt_dev_mode"))
        config.belt_template_collection_enabled = bool(
            self._value("belt_template_collection_enabled")
        )
        config.timer_reminders_enabled = config.droid_timers_enabled and bool(
            self._value("timer_reminders_enabled")
        )
        config.popup_position = str(self._value("popup_position")).strip().lower().replace(" ", "_")
        if config.popup_position not in {"top_center", "top_left", "top_right", "bottom_left", "bottom_right"}:
            config.popup_position = "top_center"
        config.sound_file = str(self._value("sound_file")).strip()
        config.ntfy_server_url = str(self._value("ntfy_server_url")).strip() or "https://ntfy.sh"
        config.ntfy_topic = str(self._value("ntfy_topic")).strip()
        config.ntfy_priority = str(self._value("ntfy_priority")).strip() or "5"
        config.ntfy_tags = str(self._value("ntfy_tags")).strip() or "rotating_light"
        config.phone_sound = str(self._value("phone_sound")).strip() or "siren"
        config.update_repo = str(self._value("update_repo")).strip() or "DogifiedV2/droidalerts"
        config.advanced_mode = bool(self._value("advanced_mode"))
        config.alert_targets = [list(combo) for combo in selected]

        # Channels that are on but not configured yet simply stay quiet until
        # their Set Up button is used, with no wizard nagging on every save.
        needs_setup = []
        if config.ntfy_enabled and not ntfy_configured(config):
            needs_setup.append("ntfy")
        if config.discord_enabled and not discord_webhook_configured(config):
            needs_setup.append("Discord")
        if config.phone_alerts_enabled and not phone_alerts_configured(config):
            needs_setup.append("Pushover")

        save_config(config)
        self.config = config
        timer_changed = any(
            getattr(previous_config, key) != getattr(config, key)
            for key in ("timer_reminders_enabled", "timer_reminder_seconds", "timer_offset_seconds")
        )
        if timer_changed and self.droid_timers is not None:
            self.hide_droid_timers()
            if config.droid_timers_enabled:
                self.show_droid_timers()
        if (
            previous_config.retention_days != config.retention_days
            or previous_config.max_storage_mb != config.max_storage_mb
        ):
            self._last_cleanup_at = 0.0
            self._refresh_storage_status()
        self.refresh_channel_statuses()
        if not update_detail:
            return config
        saved_label = "Settings saved"
        if needs_setup:
            self.detail_var.set(
                f"{saved_label}. {', '.join(needs_setup)} won't send alerts until set up"
            )
        else:
            self.detail_var.set(saved_label)
        return config

    def _value(self, key: str) -> object:
        var = self.setting_vars[key]
        if hasattr(var, "get"):
            return var.get()
        return var

    def _style_dialog_window(self, dialog: tk.Toplevel) -> None:
        try:
            dialog.configure(background=self.current_theme.colors["bg"])
        except tk.TclError:
            pass

    def _show_message(
        self,
        title: str,
        message: str,
        *,
        tone: str = "info",
    ) -> None:
        self._setup_dialog(
            title,
            intro=message,
            ok_text="Close",
            cancel_text="",
            tone=tone,
        )

    def _confirm_message(
        self,
        title: str,
        message: str,
        *,
        confirm_text: str = "Continue",
        cancel_text: str = "Cancel",
        tone: str = "primary",
    ) -> bool:
        return (
            self._setup_dialog(
                title,
                intro=message,
                ok_text=confirm_text,
                cancel_text=cancel_text,
                tone=tone,
            )
            is not None
        )

    def _setup_dialog(
        self,
        title: str,
        *,
        intro: str = "",
        steps: tuple[str, ...] = (),
        fields: tuple[tuple[str, str, str, str | None], ...] = (),
        note: str = "",
        error: str = "",
        link: tuple[str, str] | None = None,
        image_path: Path | None = None,
        ok_text: str = "Continue",
        cancel_text: str = "Cancel",
        modal: bool = True,
        tone: str = "primary",
    ) -> dict[str, str] | None:
        """Styled dialog for setup flows.

        Replaces messagebox/simpledialog so tutorials read as plain steps
        instead of a chain of bare OS prompts with generic icons. Returns the
        entered field values ({} when there are no fields) or None on cancel.
        """
        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        result: dict[str, str] | None = None

        body = ttk.Frame(dialog, padding=(22, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        wrap = 460
        row = 0

        ttk.Label(
            body,
            text=title,
            font=self._font(14, "bold"),
            **bootstyle(tone),
        ).grid(
            row=row, column=0, sticky="w", pady=(0, 10)
        )
        row += 1
        if intro:
            ttk.Label(body, text=intro, wraplength=wrap, justify="left").grid(
                row=row, column=0, sticky="w", pady=(0, 10)
            )
            row += 1
        if image_path is not None:
            try:
                dialog_image = tk.PhotoImage(file=str(image_path))
                max_image_width = max(
                    480,
                    min(1120, self.root.winfo_screenwidth() - 120),
                )
                max_image_height = max(
                    270,
                    min(630, self.root.winfo_screenheight() - 280),
                )
                divisor = max(
                    1,
                    (dialog_image.width() + max_image_width - 1) // max_image_width,
                    (dialog_image.height() + max_image_height - 1) // max_image_height,
                )
                if divisor > 1:
                    dialog_image = dialog_image.subsample(divisor, divisor)
                image_label = ttk.Label(body, image=dialog_image)
                image_label.image = dialog_image
                image_label.grid(row=row, column=0, sticky="w", pady=(0, 10))
            except (OSError, tk.TclError):
                ttk.Label(
                    body,
                    text="The belt-region example image could not be loaded.",
                    **bootstyle("danger"),
                ).grid(row=row, column=0, sticky="w", pady=(0, 10))
            row += 1
        for index, step in enumerate(steps, start=1):
            step_frame = ttk.Frame(body)
            step_frame.grid(row=row, column=0, sticky="w", pady=3)
            row += 1
            ttk.Label(
                step_frame, text=f"{index}.", font=self._font(10, "bold"), **bootstyle("info")
            ).grid(row=0, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(step_frame, text=step, wraplength=wrap - 26, justify="left").grid(
                row=0, column=1, sticky="w"
            )
        if link is not None:
            link_text, link_url = link
            ttk.Button(
                body,
                text=link_text,
                command=lambda: webbrowser.open(link_url),
                **bootstyle("info-outline"),
            ).grid(row=row, column=0, sticky="w", pady=(8, 2))
            row += 1

        entries: dict[str, StringVar] = {}
        first_entry = None
        for key, label, initial, show in fields:
            ttk.Label(body, text=label, font=self._font(10, "bold")).grid(
                row=row, column=0, sticky="w", pady=(10, 2)
            )
            row += 1
            var = StringVar(value=initial)
            entries[key] = var
            entry = ttk.Entry(body, textvariable=var, width=52, show=show or "")
            entry.grid(row=row, column=0, sticky="ew")
            row += 1
            if first_entry is None:
                first_entry = entry
        if note:
            ttk.Label(
                body,
                text=note,
                wraplength=wrap,
                justify="left",
                font=self._font(10),
            ).grid(row=row, column=0, sticky="w", pady=(10, 0))
            row += 1
        if error:
            ttk.Label(
                body, text=error, wraplength=wrap, justify="left", **bootstyle("danger")
            ).grid(row=row, column=0, sticky="w", pady=(10, 0))
            row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, sticky="e", pady=(16, 0))

        def finish(values: dict[str, str] | None) -> None:
            nonlocal result
            result = values
            dialog.destroy()

        def accept() -> None:
            finish({key: var.get().strip() for key, var in entries.items()})

        button_column = 0
        if cancel_text:
            ttk.Button(
                buttons, text=cancel_text, command=lambda: finish(None), **bootstyle("secondary")
            ).grid(row=0, column=0, padx=(0, 8))
            button_column = 1
        action_style = tone if tone in {"danger", "warning"} else "primary"
        ttk.Button(buttons, text=ok_text, command=accept, **bootstyle(action_style)).grid(
            row=0, column=button_column
        )
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: finish(None))
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        if first_entry is not None:
            first_entry.focus_set()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 3
        try:
            monitors = list_monitors()
        except Exception:
            monitors = []
        x, y = clamp_dialog_position(
            x,
            y,
            dialog.winfo_width(),
            dialog.winfo_height(),
            monitors,
        )
        dialog.geometry(format_tk_geometry(x=x, y=y))
        if modal:
            dialog.grab_set()
        dialog.wait_window()
        return result

    def _confirm_test_alert(self, channel: str) -> bool:
        return (
            self._setup_dialog(
                "Check Your Phone",
                intro=f"A test alert was just sent through {channel}. "
                "It should pop up on your phone within a few seconds.",
                ok_text="It Arrived!",
                cancel_text="No, Go Back",
            )
            is not None
        )

    def setup_ntfy_alerts_and_enable(self) -> bool:
        config = load_config()
        if self.setup_ntfy_alerts(config):
            config.ntfy_enabled = True
            config.notification_setup_prompted = True
            save_config(config)
            self.config = config
            self._set_var("ntfy_enabled", True)
            self._set_var("ntfy_server_url", config.ntfy_server_url)
            self._set_var("ntfy_topic", config.ntfy_topic)
            self._set_var("ntfy_priority", config.ntfy_priority)
            self._set_var("ntfy_tags", config.ntfy_tags)
            self.detail_var.set("ntfy alerts configured")
            return True
        self._set_var("ntfy_enabled", False)
        return False

    def setup_ntfy_alerts(self, config: AppConfig | None = None) -> bool:
        config = config or load_config()
        current_token, _source = load_ntfy_token(config)
        server_url = config.ntfy_server_url or "https://ntfy.sh"
        topic = config.ntfy_topic
        token = current_token or ""
        error = ""
        while True:
            fields: list[tuple[str, str, str, str | None]] = [
                ("topic", "Topic name", topic, None)
            ]
            note = (
                "Alerts are sent through ntfy.sh. No account needed. Turn on Advanced "
                "settings to use your own ntfy server or an access token."
            )
            if config.advanced_mode:
                fields.append(("server", "ntfy server", server_url, None))
                fields.append(("token", "Access token (leave blank unless your server needs one)", token, "*"))
                note = ""
            result = self._setup_dialog(
                "Set Up ntfy Phone Alerts",
                intro="ntfy is a free app that pushes alerts straight to your phone. "
                "It takes about two minutes:",
                steps=(
                    'Install the free "ntfy" app from the App Store or Google Play.',
                    "Open the app and tap the + button to add a subscription.",
                    "Make up a topic name nobody could guess, for example "
                    "droid_alerts_mando_4821, and subscribe to it.",
                    "Type that exact same topic name below.",
                ),
                fields=tuple(fields),
                note=note,
                error=error,
                ok_text="Send Test Alert",
            )
            if result is None:
                return False
            topic = result["topic"]
            server_url = (result.get("server") or server_url).strip().rstrip("/") or "https://ntfy.sh"
            token = result.get("token", token)
            if not valid_ntfy_server_url(server_url):
                error = "That server URL doesn't look right. It must start with http:// or https://."
                continue
            if not valid_ntfy_topic(topic):
                error = "Topic names can only use letters, numbers, underscores, or hyphens (up to 64 characters)."
                continue
            config.ntfy_server_url = server_url
            config.ntfy_topic = topic
            config.ntfy_priority = str(config.ntfy_priority or "5")
            config.ntfy_tags = str(config.ntfy_tags or "rotating_light")
            save_ntfy_token(config, token)
            self.detail_var.set(f"ntfy topic saved: {server_url}/{topic}")
            try:
                send_ntfy_test_alert(config)
            except Exception as exc:
                error = f"Sending the test alert failed: {exc}"
                continue
            if self._confirm_test_alert("ntfy"):
                return True
            error = (
                "No alert? Make sure the topic below matches the one in the ntfy app "
                "exactly, then send another test."
            )

    def setup_discord_alerts_and_enable(self) -> bool:
        config = load_config()
        if self.setup_discord_alerts(config):
            config.discord_enabled = True
            save_config(config)
            self.config = config
            self._set_var("discord_enabled", True)
            self.detail_var.set("Discord alerts configured")
            return True
        self._set_var("discord_enabled", False)
        return False

    def setup_discord_alerts(self, config: AppConfig | None = None) -> bool:
        config = config or load_config()
        current, _source = load_discord_webhook(config)
        webhook_url = current or ""
        error = ""
        while True:
            result = self._setup_dialog(
                "Set Up Discord Alerts",
                intro="Droid Alerts can post priority spawns into a Discord channel "
                "using a webhook:",
                steps=(
                    "In Discord, open the server channel where alerts should go and "
                    "click the gear icon next to its name (Edit Channel).",
                    'Go to Integrations, then Webhooks, and click "New Webhook".',
                    'Click "Copy Webhook URL" and paste it below.',
                ),
                fields=(("webhook", "Webhook URL", webhook_url, None),),
                error=error,
                ok_text="Save Webhook",
            )
            if result is None:
                return False
            webhook_url = result["webhook"].lstrip("\ufeff")
            if not valid_discord_webhook_url(webhook_url):
                error = (
                    "That doesn't look like a Discord webhook URL \u2014 it should start "
                    "with https://discord.com/api/webhooks/."
                )
                continue
            path = save_discord_webhook(config, webhook_url)
            self.detail_var.set(f"Discord webhook saved to {path}")
            return True

    def setup_phone_alerts_and_enable(self) -> bool:
        config = load_config()
        if self.setup_phone_alerts(config):
            config.phone_alerts_enabled = True
            save_config(config)
            self.config = config
            self._set_var("phone_alerts_enabled", True)
            self.detail_var.set("Pushover alerts configured")
            return True
        self._set_var("phone_alerts_enabled", False)
        return False

    def setup_phone_alerts(self, config: AppConfig | None = None) -> bool:
        config = config or load_config()
        credentials_error = ""
        try:
            existing, _source = load_phone_alert_credentials(config)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            existing = None
            credentials_error = f"The existing credentials file could not be read: {exc}"
        token = (existing or {}).get("token", "")
        user = (existing or {}).get("user", "")
        error = credentials_error
        while True:
            result = self._setup_dialog(
                "Set Up Pushover Phone Alerts",
                intro="Pushover is a phone app Droid Alerts can send alerts through. "
                "You only set this up once:",
                steps=(
                    "Go to pushover.net, create a free account, then install the "
                    "Pushover app on your phone and log in.",
                    'Back on the pushover.net home page, copy "Your User Key" '
                    "(shown near the top-right) into the User Key box below.",
                    'Further down that page, click "Create an Application/API Token". '
                    "Name it anything (like Droid Alerts), create it, and copy the "
                    "token it gives you into the API Token box.",
                ),
                fields=(
                    ("user", "User Key", user, None),
                    ("token", "API Token", token, None),
                ),
                link=("Open pushover.net", "https://pushover.net"),
                error=error,
                ok_text="Send Test Alert",
            )
            if result is None:
                return False
            user = result["user"]
            token = result["token"]
            if not token or not user:
                error = "Both boxes need to be filled in before the test can be sent."
                continue
            path = save_phone_credentials(config, token, user)
            self.detail_var.set(f"Pushover credentials saved to {path}")
            credentials = {"token": token, "user": user}
            try:
                send_phone_test_alert(credentials, sound=config.phone_sound)
            except Exception as exc:
                error = f"Sending the test alert failed: {exc}"
                continue
            if self._confirm_test_alert("Pushover"):
                return True
            error = (
                "No alert? Double-check both keys against the pushover.net page, "
                "then send another test."
            )

    def on_ntfy_alert_toggle(self) -> None:
        if not bool(self._value("ntfy_enabled")):
            self.refresh_channel_statuses()
            return
        config = load_config()
        config.ntfy_server_url = str(self._value("ntfy_server_url")).strip() or "https://ntfy.sh"
        config.ntfy_topic = str(self._value("ntfy_topic")).strip()
        if not ntfy_configured(config) and not self.setup_ntfy_alerts_and_enable():
            self.detail_var.set("ntfy alerts stay off until a topic is set up")
        self.refresh_channel_statuses()

    def on_discord_alert_toggle(self) -> None:
        if not bool(self._value("discord_enabled")):
            self.refresh_channel_statuses()
            return
        if not discord_webhook_configured(load_config()) and not self.setup_discord_alerts_and_enable():
            self.detail_var.set("Discord alerts stay off until a webhook is set up")
        self.refresh_channel_statuses()

    def on_phone_alert_toggle(self) -> None:
        if not bool(self._value("phone_alerts_enabled")):
            self.refresh_channel_statuses()
            return
        if not phone_alerts_configured(load_config()) and not self.setup_phone_alerts_and_enable():
            self.detail_var.set("Pushover alerts stay off until the keys are set up")
        self.refresh_channel_statuses()

    @staticmethod
    def _test_detection() -> Detection:
        return Detection(
            droid="Beskar",
            rarity="Mythic",
            row_box=(0, 0, 480, 44),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="gui-test",
            shape_score=1.0,
        )

    def send_channel_test(self, channel: str) -> None:
        config = self.save_settings(interactive=False)
        if config is None:
            return
        self._send_channel_test_with_config(channel, config, self._test_detection())

    def _send_channel_test_with_config(self, channel: str, config: AppConfig, detection: Detection) -> bool:
        label = {
            "popup": "Popup",
            "sound": "Sound",
            "discord": "Discord",
            "ntfy": "ntfy",
            "pushover": "Pushover",
        }.get(channel, channel)
        if channel == "popup":
            show_popup(
                detection,
                config.popup_seconds,
                icon_path=popup_icon_path(config),
                parent=self.root,
                monitor=self._current_monitor_info(),
                position=config.popup_position,
                scale=config.popup_scale,
                opacity=config.popup_opacity,
            )
            self.channel_status_vars["Popup"].set("Previewed just now")
            return True
        if channel == "sound":
            policy = AlertPolicy(config)
            policy.sound_enabled = True
            try:
                policy.notify(detection)
            except Exception as exc:
                self.channel_status_vars["Sound"].set("Failed to play")
                self.detail_var.set(f"Sound test failed: {exc}")
                return False
            self.channel_status_vars["Sound"].set("Played just now")
            return True

        target = None
        args: tuple[object, ...] = ()
        kwargs: dict[str, object] = {}
        if channel == "discord":
            webhook_url, _source = load_discord_webhook(config)
            if not webhook_url:
                self.channel_status_vars[label].set("Not configured")
                self.detail_var.set("Set up Discord before testing it")
                return False
            target, args = send_discord_alert, (webhook_url, detection)
        elif channel == "ntfy":
            if not ntfy_configured(config):
                self.channel_status_vars[label].set("Not configured")
                self.detail_var.set("Set up ntfy before testing it")
                return False
            target, args, kwargs = send_ntfy_alert, (config, detection), {"attachment_path": None}
        elif channel == "pushover":
            try:
                credentials, _source = load_phone_alert_credentials(config)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                self.channel_status_vars[label].set("Invalid credentials file")
                self.detail_var.set(f"Pushover credentials could not be read: {exc}")
                return False
            if not credentials:
                self.channel_status_vars[label].set("Not configured")
                self.detail_var.set("Set up Pushover before testing it")
                return False
            target, args, kwargs = (
                send_phone_alert,
                (credentials, detection),
                {"sound": config.phone_sound, "attachment_path": None},
            )
        if target is None:
            return False

        self.channel_status_vars[label].set("Sending…")

        def worker() -> None:
            try:
                result = target(*args, **kwargs)
            except Exception as exc:
                result = None
                error = str(exc)
            else:
                error = ""
            self._post_to_ui(lambda: self._channel_test_done(label, result, error))

        threading.Thread(target=worker, name=f"DroidAlertsTest{label}", daemon=True).start()
        return True

    def _channel_test_done(self, label: str, result, error: str) -> None:
        success = bool(result is not None and getattr(result, "success", False))
        detail = str(getattr(result, "message", "") or error)
        if success:
            self.channel_status_vars[label].set("Test delivered just now")
            self.detail_var.set(f"{label} test alert delivered successfully")
        else:
            self.channel_status_vars[label].set(f"Failed · {detail[:70]}")
            self.detail_var.set(f"{label} test failed: {detail}")

    def send_test_alert(self) -> None:
        config = self.save_settings()
        if config is None:
            return
        detection = self._test_detection()
        started: list[str] = []
        if config.popup_enabled:
            self._send_channel_test_with_config("popup", config, detection)
            started.append("popup")
        if config.sound_enabled:
            self._send_channel_test_with_config("sound", config, detection)
            started.append("sound")
        if config.discord_enabled:
            if self._send_channel_test_with_config("discord", config, detection):
                started.append("Discord")
        if config.ntfy_enabled:
            if self._send_channel_test_with_config("ntfy", config, detection):
                started.append("ntfy")
        if config.phone_alerts_enabled:
            if self._send_channel_test_with_config("pushover", config, detection):
                started.append("Pushover")
        if started:
            remote = [name for name in started if name not in {"popup", "sound"}]
            suffix = " Remote delivery results will appear beside each channel." if remote else ""
            self.detail_var.set(f"Testing: {', '.join(started)}. {event_text(detection)}.{suffix}")
        else:
            self.detail_var.set("No alert channels are enabled")

    def start_watcher(self) -> None:
        if self.is_watching():
            self.detail_var.set("Watcher is already running")
            return
        config = self.save_settings()
        if config is None:
            return
        if config.capture_source == "device":
            try:
                self._ensure_device_capture_session(config)
            except Exception as exc:
                self._set_watcher_state("Error")
                self.watcher_status_var.set("Capture device unavailable")
                self.watcher_detail_var.set(str(exc))
                self.detail_var.set(str(exc))
                return
        self.stop_event = threading.Event()
        self._watch_stop_reason = ""
        self._watch_segment_started = time.monotonic()
        self.watch_thread = threading.Thread(target=self._watch_thread, args=(config, self.stop_event), daemon=True)
        self.watch_thread.start()
        self._set_watcher_state("Running")
        self.watcher_status_var.set("Starting screen capture…")
        self.watcher_detail_var.set(f"Preparing {self._capture_target_label(config)}")
        mode = "debug on" if config.save_debug_screenshots else "debug off"
        self.detail_var.set(f"Watcher started ({mode})")

    def _watch_thread(self, config: AppConfig, stop_event: threading.Event) -> None:
        thread = threading.current_thread()
        try:
            run_watch(
                debug=config.save_debug_screenshots,
                config=config,
                stop_event=stop_event,
                popup_parent=self.root,
                status_callback=self._queue_watcher_status,
                capture_factory=self._create_runtime_capture,
            )
            self._post_to_ui(lambda thread=thread: self._watcher_finished(None, thread))
        except Exception as exc:
            self._post_to_ui(lambda exc=exc, thread=thread: self._watcher_finished(exc, thread))

    def _watcher_finished(self, exc: Exception | None, thread: threading.Thread | None = None) -> None:
        if thread is not None and self.watch_thread is not thread:
            return
        reason = self._watch_stop_reason
        if self._watch_segment_started is not None:
            self.session_monitoring_seconds += time.monotonic() - self._watch_segment_started
            self._watch_segment_started = None
        self.watch_thread = None
        self.stop_event = None
        if exc is None:
            self._set_watcher_state("Stopped")
            self.watcher_status_var.set("Ready to watch")
            self.watcher_detail_var.set(self._watcher_ready_text())
            self.detail_var.set("Watcher stopped")
        else:
            self._set_watcher_state("Error")
            self.watcher_status_var.set("Monitoring stopped unexpectedly")
            self.watcher_detail_var.set(str(exc))
            self.detail_var.set(f"Watcher stopped: {exc}")
            self._show_message("Watcher", str(exc), tone="danger")
        self._watch_stop_reason = ""
        self._maybe_close_device_capture_session()

    def stop_watcher(self, *, reason: str = "manual") -> None:
        if self.stop_event is not None:
            self._watch_stop_reason = reason
            self.stop_event.set()
            self.watch_button.configure(text="Stopping…", state="disabled")
            self.detail_var.set("Stopping watcher…")
        else:
            self.detail_var.set("Watcher is not running")

    def _schedule_log_refresh(self) -> None:
        self._log_refresh_after_id = self.root.after(2000, self._auto_refresh_logs)

    def _auto_refresh_logs(self) -> None:
        try:
            self.refresh_logs(update_detail=False, only_if_changed=True)
        except Exception as exc:
            print(f"[GUI] History refresh failed: {exc}")
        finally:
            if not self._shutting_down:
                self._schedule_log_refresh()

    def refresh_logs(self, *, update_detail: bool = True, only_if_changed: bool = False) -> None:
        path = logs_dir() / "events.jsonl"
        if not path.exists():
            for item in self.logs_tree.get_children():
                self.logs_tree.delete(item)
            self.history_rows_by_item.clear()
            self._log_file_signature = None
            self.history_summary_var.set("No history yet")
            return
        try:
            stat = path.stat()
        except OSError as exc:
            if update_detail:
                self.detail_var.set(f"Could not read logs: {exc}")
            return
        signature = (stat.st_mtime_ns, stat.st_size)
        if only_if_changed and signature == self._log_file_signature:
            return
        try:
            raw_lines = read_last_lines(path, max_lines=3000)
        except OSError as exc:
            if update_detail:
                self.detail_var.set(f"Could not read logs: {exc}")
            return

        for item in self.logs_tree.get_children():
            self.logs_tree.delete(item)

        rows: list[dict[str, object]] = []
        selected_filter = self.history_filter_var.get()
        search_text = self.history_search_var.get().strip().casefold()
        for line in reversed(raw_lines):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            event_type = str(row.get("event_type") or "")
            is_debug_row = self._log_row_is_debug(row)
            if selected_filter == "All" and is_debug_row:
                continue
            if selected_filter == "Priority alerts" and not (
                event_type == "alert"
                or (event_type.startswith("belt_") and bool(row.get("alerted")))
                or (not event_type and bool(row.get("alerted")))
            ):
                continue
            if selected_filter == "Belt Tracker" and not (
                str(row.get("source") or "") == "belt_tracker" or event_type.startswith("belt_")
            ):
                continue
            if selected_filter == "Detections" and event_type not in {
                "alert",
                "detected",
                "seen",
                "belt_entered",
            }:
                continue
            if selected_filter == "Delivery failures" and not (
                event_type == "delivery" and not bool(row.get("success"))
            ):
                continue
            if selected_filter == "Debug" and not is_debug_row:
                continue
            if search_text and search_text not in json.dumps(row, ensure_ascii=False).casefold():
                continue
            rows.append(row)
            if len(rows) >= 500:
                break

        self.history_rows_by_item.clear()
        priority_count = 0
        for row in rows:
            event_type = str(row.get("event_type") or "")
            success = row.get("success")
            if event_type == "delivery":
                status = "Delivered" if bool(success) else "Failed"
            elif row.get("alerted"):
                status = "Alerted"
                priority_count += 1
            elif self._log_row_is_debug(row):
                status = "Debug"
            else:
                status = "Detected"
            tag = "failure" if status == "Failed" else ("priority" if status == "Alerted" else ("success" if status == "Delivered" else "muted"))
            item = self.logs_tree.insert(
                "",
                "end",
                values=(
                    self._history_time(str(row.get("ts", ""))),
                    self._log_row_type(row).title(),
                    row.get("droid", ""),
                    row.get("rarity", ""),
                    status,
                    self._log_row_info(row),
                ),
                tags=(tag,) if tag else (),
            )
            self.history_rows_by_item[item] = row
        self.history_summary_var.set(f"{len(rows)} shown · {priority_count} priority alert(s)")
        self._log_file_signature = signature

    @staticmethod
    def _history_time(value: str) -> str:
        if len(value) >= 15 and value[8] == "_":
            return f"{value[0:4]}-{value[4:6]}-{value[6:8]} {value[9:11]}:{value[11:13]}:{value[13:15]}"
        return value

    def show_history_details(self, _event=None) -> None:
        selection = self.logs_tree.selection()
        if not selection:
            return
        row = self.history_rows_by_item.get(selection[0])
        if row is None:
            return
        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title("History Event Details")
        dialog.transient(self.root)
        dialog.geometry("720x520")
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            text=f"{self._log_row_type(row).title()} · {row.get('rarity', '')} {row.get('droid', '')}",
            font=self._font(14, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        colors = self.current_theme.colors
        text = tk.Text(
            body,
            wrap="word",
            bg=colors["inputbg"],
            fg=colors["inputfg"],
            insertbackground=colors["primary"],
            selectbackground=colors["selectbg"],
            selectforeground=colors["selectfg"],
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
        )
        text.grid(row=1, column=0, sticky="nsew")
        text.insert("1.0", json.dumps(row, indent=2, ensure_ascii=False))
        text.configure(state="disabled")
        detail_path = str(row.get("sample_path") or row.get("detail") or "").split(";", 1)[0].strip()
        related_path = Path(detail_path) if detail_path else None
        if related_path is not None and related_path.exists() and related_path.suffix.lower() == ".png":
            try:
                photo = tk.PhotoImage(file=str(related_path))
                factor = max(1, (photo.width() + 639) // 640, (photo.height() + 179) // 180)
                preview = photo.subsample(factor)
                preview_label = ttk.Label(body, image=preview, anchor="center")
                preview_label.image = preview
                preview_label.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            except Exception:
                pass
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="e", pady=(10, 0))
        if related_path is not None and related_path.exists():
            ttk.Button(buttons, text="Open Related File", command=lambda: self.open_path(related_path)).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Close", command=dialog.destroy, **bootstyle("secondary")).pack(side="left")

    def export_history_csv(self) -> None:
        if not self.history_rows_by_item:
            self._show_message(
                "Export History",
                "There are no visible history rows to export.",
            )
            return
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export visible history",
            defaultextension=".csv",
            initialfile="droid_alerts_history.csv",
            filetypes=(("CSV file", "*.csv"),),
        )
        if not target:
            return
        fieldnames = sorted({key for row in self.history_rows_by_item.values() for key in row})
        try:
            with Path(target).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for row in self.history_rows_by_item.values():
                    writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
        except OSError as exc:
            self._show_message("Export History", str(exc), tone="danger")
            return
        self.detail_var.set(f"History exported to {target}")

    def _log_row_is_debug(self, row: dict[str, object]) -> bool:
        if bool(row.get("debug")):
            return True
        return str(row.get("event_type", "")) in {"seen", "rejected", "debug_snapshot"}

    def _log_row_type(self, row: dict[str, object]) -> str:
        event_type = str(row.get("event_type", "")).strip()
        if event_type:
            return event_type.replace("_", " ")
        return "alert" if row.get("alerted") else "detected"

    def _log_row_info(self, row: dict[str, object]) -> str:
        reason = str(row.get("reason", "") or "")
        detail = str(row.get("detail", "") or "")
        if reason and detail:
            return f"{reason}: {detail}"
        channel = str(row.get("channel", "") or "")
        if channel and detail:
            return f"{channel}: {detail}"
        return reason or detail or channel or str(row.get("scale_method", "") or "")

    def _log_row_key(self, row: dict[str, object]) -> str:
        row_box = row.get("row_box")
        y_bucket = ""
        if isinstance(row_box, list) and len(row_box) >= 4:
            try:
                y_bucket = str(((int(row_box[1]) + int(row_box[3])) // 2) // 32)
            except (TypeError, ValueError):
                y_bucket = ""
        return (
            f"{row.get('droid', '')}|{row.get('rarity', '')}|"
            f"{y_bucket}|{bool(row.get('alerted'))}"
        )

    def open_path(self, path: Path) -> None:
        try:
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.touch()
            else:
                path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self._show_message("Open", str(exc), tone="danger")

    def open_region_positioner(self) -> None:
        if self.region_positioner is not None:
            try:
                if self.region_positioner.winfo_exists():
                    self.region_positioner.lift()
                    self.region_positioner.focus_force()
                    return
            except Exception:
                self.region_positioner = None

        if self.region_overlay is None or not self.region_overlay.winfo_exists():
            try:
                self.toggle_region_overlay()
            except Exception as exc:
                self._show_message("Move Chat Box", str(exc), tone="danger")
                return
        if self.region_box is None:
            return

        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        self.region_positioner = dialog
        dialog.title("Move Chat Box")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.protocol("WM_DELETE_WINDOW", self.close_region_overlay)

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Move the chat capture box", font=self._font(14, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="Use the arrows to reposition the red box. Its width and height stay fixed.",
            wraplength=360,
            justify="left",
            **muted_style(),
        ).pack(anchor="w", pady=(4, 12))

        arrows = ttk.Frame(body)
        arrows.pack()
        ttk.Button(arrows, text="↑ Up", width=10, command=lambda: self.nudge_region(0, -10)).grid(
            row=0, column=1, padx=4, pady=4
        )
        ttk.Button(arrows, text="← Left", width=10, command=lambda: self.nudge_region(-10, 0)).grid(
            row=1, column=0, padx=4, pady=4
        )
        ttk.Label(arrows, text="10 px", anchor="center", width=10, **muted_style()).grid(
            row=1, column=1, padx=4, pady=4
        )
        ttk.Button(arrows, text="Right →", width=10, command=lambda: self.nudge_region(10, 0)).grid(
            row=1, column=2, padx=4, pady=4
        )
        ttk.Button(arrows, text="↓ Down", width=10, command=lambda: self.nudge_region(0, 10)).grid(
            row=2, column=1, padx=4, pady=4
        )
        ttk.Label(body, textvariable=self.region_status_var, **muted_style()).pack(anchor="center", pady=(8, 10))
        ttk.Button(body, text="Done", command=self.close_region_overlay, width=18, **bootstyle("success")).pack()

        dialog.bind("<Left>", lambda _event: self.nudge_region(-10, 0))
        dialog.bind("<Right>", lambda _event: self.nudge_region(10, 0))
        dialog.bind("<Up>", lambda _event: self.nudge_region(0, -10))
        dialog.bind("<Down>", lambda _event: self.nudge_region(0, 10))
        dialog.bind("<Return>", lambda _event: self.close_region_overlay())
        dialog.bind("<Escape>", lambda _event: self.close_region_overlay())

        self.region_status_var.set(
            f"Left {self.region_box.left}px · Top {self.region_box.top}px"
        )
        dialog.update_idletasks()
        monitor = self._current_monitor_info()
        if monitor is not None:
            x = monitor.left + max(20, monitor.width - dialog.winfo_width() - 40)
            y = monitor.top + 80
        else:
            x = self.root.winfo_rootx() + max(20, self.root.winfo_width() - dialog.winfo_width() - 30)
            y = self.root.winfo_rooty() + 80
        dialog.geometry(format_tk_geometry(x=x, y=y))
        dialog.focus_force()

    def create_diagnostics_bundle(self) -> None:
        self.detail_var.set("Creating support bundle…")
        config = load_config()

        def worker() -> None:
            try:
                path = create_support_bundle(config)
            except Exception as exc:
                self._post_to_ui(
                    lambda exc=exc: self._show_message(
                        "Support Bundle",
                        str(exc),
                        tone="danger",
                    )
                )
                return
            self._post_to_ui(lambda path=path: self._support_bundle_ready(path))

        threading.Thread(target=worker, name="DroidAlertsSupportBundle", daemon=True).start()

    def _support_bundle_ready(self, path: Path) -> None:
        self.detail_var.set(f"Support bundle created: {path.name}")
        self._refresh_storage_status(cleanup=False)
        if self._confirm_message(
            "Support Bundle",
            f"Created a redacted support bundle:\n\n{path}\n\nOpen its folder?",
            confirm_text="Open Folder",
        ):
            self.open_path(path.parent)

    def clear_debug_data(self) -> None:
        if not self._confirm_message(
            "Clear Debug Captures",
            "Delete all locally saved debug screenshots?",
            confirm_text="Delete Captures",
            tone="danger",
        ):
            return
        result = clear_debug_captures()
        self.detail_var.set(f"Deleted {result.deleted_files} debug file(s), freeing {format_bytes(result.freed_bytes)}")
        self._refresh_storage_status(cleanup=False)

    def clear_history_data(self) -> None:
        if not self._confirm_message(
            "Clear History",
            "Delete all event history? This cannot be undone.",
            confirm_text="Clear History",
            tone="danger",
        ):
            return
        result = clear_history()
        self._log_file_signature = None
        self.refresh_logs(update_detail=False)
        self.detail_var.set(f"Deleted {result.deleted_files} history file(s), freeing {format_bytes(result.freed_bytes)}")
        self._refresh_storage_status(cleanup=False)

    def _refresh_storage_status(self, *, cleanup: bool = True) -> None:
        config = load_config()

        def worker() -> None:
            now = time.monotonic()
            if cleanup and now - self._last_cleanup_at >= 3600:
                cleanup_result = cleanup_runtime_data(config.retention_days, config.max_storage_mb)
                self._last_cleanup_at = now
            else:
                cleanup_result = None
            summary = storage_summary()
            self._post_to_ui(lambda: self._storage_status_ready(summary, cleanup_result))

        threading.Thread(target=worker, name="DroidAlertsStorage", daemon=True).start()

    def _storage_status_ready(self, summary: dict[str, int], cleanup) -> None:
        text = (
            f"Runtime data: {format_bytes(summary['total'])}\n"
            f"History {format_bytes(summary['logs'])} · Alert samples {format_bytes(summary['samples'])} · "
            f"Debug {format_bytes(summary['debug'])} · Belt dev {format_bytes(summary['belt_dev'])}"
        )
        if cleanup is not None and cleanup.deleted_files:
            text += f"\nAutomatic cleanup removed {cleanup.deleted_files} file(s)."
        self.storage_status_var.set(text)
        if self._storage_after_id is not None:
            try:
                self.root.after_cancel(self._storage_after_id)
            except Exception:
                pass
        self._storage_after_id = self.root.after(3600000, self._refresh_storage_status)

    def toggle_region_overlay(self) -> None:
        if self.region_overlay is not None and self.region_overlay.winfo_exists():
            self.close_region_overlay()
            return
        set_dpi_awareness()
        capture = self._create_chat_capture()
        try:
            screen_w, screen_h = capture.screen_size()
            capture_area = self._capture_area(capture)
            box, source = RegionResolver(
                screen_w,
                screen_h,
                max_failures=self.config.validation_failures_before_calibration_prompt,
                monitor_key=getattr(capture_area, "key", None),
            ).resolve()
            if self.config.capture_source == "device":
                self._show_device_chat_region_preview(capture, box, source, screen_w, screen_h)
                self.set_region_controls_visible(True)
                return
            left_offset = int(getattr(capture_area, "left", 0))
            top_offset = int(getattr(capture_area, "top", 0))
            self.region_box = box
            self.region_source = source
            self.region_screen_size = (screen_w, screen_h)
            self.region_monitor_offset = (left_offset, top_offset)
            self.region_monitor_key = getattr(capture_area, "key", None)
            self.show_region_overlay(
                left_offset + box.left,
                top_offset + box.top,
                box.width,
                box.height,
                source,
            )
            self.set_region_controls_visible(True)
        finally:
            capture.close()

    def _show_device_chat_region_preview(
        self,
        capture,
        box: PixelBox,
        source: str,
        screen_w: int,
        screen_h: int,
    ) -> None:
        frame = capture.grab(PixelBox(0, 0, screen_w, screen_h))
        cv2.rectangle(
            frame,
            (box.left, box.top),
            (max(box.left, box.right - 1), max(box.top, box.bottom - 1)),
            (0, 0, 255),
            max(2, round(screen_h / 540)),
        )
        scale = min(1.0, 1100 / max(1, screen_w), 650 / max(1, screen_h))
        if scale < 1.0:
            frame = cv2.resize(
                frame,
                (max(1, round(screen_w * scale)), max(1, round(screen_h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        encoded_ok, encoded = cv2.imencode(".png", frame)
        if not encoded_ok:
            raise RuntimeError("Could not prepare the capture-device preview.")
        temp = tempfile.NamedTemporaryFile(
            prefix="droid_alerts_device_region_", suffix=".png", delete=False
        )
        temp_path = Path(temp.name)
        try:
            temp.write(encoded.tobytes())
            temp.close()
            preview = tk.PhotoImage(file=str(temp_path))
        finally:
            temp_path.unlink(missing_ok=True)

        dialog = tk.Toplevel(self.root)
        self._style_dialog_window(dialog)
        dialog.title("Chat Region Preview")
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=f"Capture device · {screen_w} × {screen_h} · Region: {source}",
            font=self._font(11, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        image_label = ttk.Label(body, image=preview)
        image_label.image = preview
        image_label.pack()
        ttk.Label(
            body,
            text="The red box is the area scanned for chat alerts.",
            **muted_style(),
        ).pack(anchor="w", pady=(10, 0))
        ttk.Button(body, text="Close", command=dialog.destroy, **bootstyle("secondary")).pack(
            anchor="e", pady=(12, 0)
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self.region_box = box
        self.region_source = source
        self.region_screen_size = (screen_w, screen_h)
        self.region_monitor_key = self._current_capture_key()

    def close_region_overlay(self) -> None:
        if self.region_positioner is not None:
            try:
                if self.region_positioner.winfo_exists():
                    self.region_positioner.destroy()
            except Exception:
                pass
            self.region_positioner = None
        self.destroy_region_overlay_windows()
        self.region_box = None
        self.region_source = ""
        self.region_screen_size = None
        self.region_monitor_offset = (0, 0)
        self.region_monitor_key = None
        self.set_region_controls_visible(False)

    def destroy_region_overlay_windows(self) -> None:
        for overlay in self.region_overlay_windows:
            try:
                if overlay.winfo_exists():
                    overlay.destroy()
            except Exception:
                pass
        if self.region_overlay is not None:
            try:
                if self.region_overlay.winfo_exists():
                    self.region_overlay.destroy()
            except Exception:
                pass
        self.region_overlay = None
        self.region_overlay_windows = []

    def set_region_controls_visible(self, visible: bool) -> None:
        if hasattr(self, "region_adjust_frame"):
            if visible:
                self.region_adjust_frame.grid()
            else:
                self.region_adjust_frame.grid_remove()
                self.region_status_var.set("")
        if hasattr(self, "region_button"):
            self.region_button.configure(text="Hide Chat Region" if visible else "Show Chat Region")

    def nudge_region(self, delta_x: int, delta_y: int) -> None:
        if self.region_box is None or self.region_screen_size is None:
            return
        screen_w, screen_h = self.region_screen_size
        max_left = max(0, screen_w - self.region_box.width)
        max_top = max(0, screen_h - self.region_box.height)
        left = max(0, min(max_left, self.region_box.left + delta_x))
        top = max(0, min(max_top, self.region_box.top + delta_y))
        self.region_box = PixelBox(
            left=left,
            top=top,
            width=self.region_box.width,
            height=self.region_box.height,
        )
        self.region_source = "manual(moved)"
        Calibration(
            mode="manual",
            ratios={
                "left": self.region_box.left / max(1, screen_w),
                "top": self.region_box.top / max(1, screen_h),
                "width": self.region_box.width / max(1, screen_w),
                "height": self.region_box.height / max(1, screen_h),
            },
            monitor_signature={"width": screen_w, "height": screen_h},
        ).save(self.region_monitor_key)
        self.destroy_region_overlay_windows()
        left_offset, top_offset = self.region_monitor_offset
        self.show_region_overlay(
            left_offset + self.region_box.left,
            top_offset + self.region_box.top,
            self.region_box.width,
            self.region_box.height,
            self.region_source,
        )
        self.set_region_controls_visible(True)
        self.region_status_var.set(
            f"Saved · Left {self.region_box.left}px · Top {self.region_box.top}px"
        )

    def auto_detect_region(self) -> None:
        Calibration().save(self._current_capture_key())
        note = "Automatic region saved and applied"
        overlay_was_open = self.region_overlay is not None and self.region_overlay.winfo_exists()
        if overlay_was_open:
            self.destroy_region_overlay_windows()
            self.region_box = None
            self.region_source = ""
            self.region_screen_size = None
            self.toggle_region_overlay()
            self.region_status_var.set(note)
        else:
            self.detail_var.set(note)

    def show_region_overlay(self, left: int, top: int, width: int, height: int, source: str) -> None:
        self.destroy_region_overlay_windows()
        color = "#ff1744"
        thickness = 5
        left = int(left)
        top = int(top)
        width = max(1, int(width))
        height = max(1, int(height))
        thickness = min(thickness, max(1, width), max(1, height))
        windows: list[tk.Toplevel] = []

        def add_bar(x: int, y: int, w: int, h: int) -> None:
            if w <= 0 or h <= 0:
                return
            bar = tk.Toplevel(self.root)
            bar.overrideredirect(True)
            bar.attributes("-topmost", True)
            bar.configure(bg=color)
            bar.geometry(format_tk_geometry(width=w, height=h, x=x, y=y))
            windows.append(bar)

        add_bar(left, top, width, thickness)
        add_bar(left, top + height - thickness, width, thickness)
        add_bar(left, top, thickness, height)
        add_bar(left + width - thickness, top, thickness, height)

        title = f"Droid Alerts region: {source}"
        label_height = 24
        monitor_top = self.region_monitor_offset[1]
        if top - monitor_top >= label_height + 4:
            label = tk.Toplevel(self.root)
            label.overrideredirect(True)
            label.attributes("-topmost", True)
            label.configure(bg=color)
            label_width = min(width, max(230, min(420, len(title) * 9 + 18)))
            label.geometry(
                format_tk_geometry(
                    width=label_width,
                    height=label_height,
                    x=left,
                    y=top - label_height - 2,
                )
            )
            tk.Label(
                label,
                text=title,
                bg=color,
                fg="white",
                anchor="w",
                font=self._font(10, "bold"),
                padx=8,
            ).pack(fill="both", expand=True)
            windows.append(label)

        self.region_overlay_windows = windows
        self.region_overlay = windows[0] if windows else None

    def _poll_for_updates(self) -> None:
        self._update_poll_after_id = None
        self.check_updates(manual=False)
        self._update_poll_after_id = self.root.after(
            UPDATE_POLL_INTERVAL_MS,
            self._poll_for_updates,
        )

    def check_updates(self, *, manual: bool) -> None:
        if self.update_check_running:
            return
        config = load_config()
        if not manual and not config.update_check_enabled:
            return
        if manual:
            # A direct user request should work even when background checks are off.
            config.update_check_enabled = True
        self.update_check_running = True

        def worker() -> None:
            try:
                release = check_for_update(config)
            except Exception as exc:
                self._post_to_ui(lambda exc=exc: self._update_check_done(None, exc, manual))
                return
            self._post_to_ui(lambda release=release: self._update_check_done(release, None, manual))

        threading.Thread(target=worker, daemon=True).start()

    def _update_check_done(
        self,
        release: dict[str, str] | None,
        exc: Exception | None,
        manual: bool,
    ) -> None:
        self.update_check_running = False
        if exc is not None:
            if manual:
                self._show_message("Updates", str(exc), tone="danger")
            return
        if not release:
            if manual:
                self.detail_var.set("No newer GitHub release found")
            return

        self.available_update = release
        self.update_ready_button.configure(text="Update ready!", state="normal")
        self.update_ready_button.grid()
        self.detail_var.set(f"{release['name']} is ready to install")
        if manual:
            self.show_available_update()

    def show_available_update(self) -> None:
        release = self.available_update
        if release is None:
            self.check_updates(manual=True)
            return
        choice = self._setup_dialog(
            "Update Available",
            intro=f"{release['name']} is available.\n\n"
            "Do you want to install it? The new files will be downloaded and "
            "Droid Alerts will restart itself. Your settings, alerts and "
            "captures are kept.",
            link=("View What's New", release["url"]),
            ok_text="Install & Restart",
            cancel_text="Not Now",
        )
        if choice is not None:
            self._install_update(release)

    def _install_update(self, release: dict[str, str]) -> None:
        from .updater import download_and_install_update, preferred_update_url

        self.update_ready_button.configure(text="Updating…", state="disabled")
        self.detail_var.set(f"Downloading update {release['tag']}...")

        def progress(text: str) -> None:
            self._post_to_ui(lambda: self.detail_var.set(text))

        def worker() -> None:
            try:
                result = download_and_install_update(
                    preferred_update_url(release),
                    release["tag"],
                    progress=progress,
                )
            except Exception as exc:
                self._post_to_ui(lambda exc=exc: self._install_update_failed(release, exc))
                return
            self._post_to_ui(lambda: self._restart_after_update(result.external_restart))

        threading.Thread(target=worker, daemon=True).start()

    def _install_update_failed(self, release: dict[str, str], exc: Exception) -> None:
        self.update_ready_button.configure(text="Update ready!", state="normal")
        self.detail_var.set(f"Update failed: {exc}")
        choice = self._setup_dialog(
            "Update Failed",
            intro="The update couldn't be installed automatically:\n\n"
            f"{exc}\n\n"
            "Nothing was changed. You can download it manually from the "
            "GitHub release page instead.",
            ok_text="Open Release Page",
            cancel_text="Close",
        )
        if choice is not None:
            webbrowser.open(release["url"])

    def _restart_after_update(self, external_restart: bool = False) -> None:
        from .updater import exit_for_external_update, restart_program

        self._shutting_down = True
        self._belt_restart_after_stop = False
        self.detail_var.set("Update installed, restarting...")
        self.app_telemetry.stop()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.belt_stop_event is not None:
            self._belt_stop_reason = "update"
            self.belt_stop_event.set()
        self._terminate_belt_process()
        self._maybe_close_device_capture_session(force=True)
        self.belt_overlay.close()
        self.hide_droid_timers()
        self.destroy_region_overlay_windows()
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        # Give the watcher thread a moment to notice the stop event, then
        # hand off to the freshly installed code.
        if external_restart:
            self.root.after(700, exit_for_external_update)
        else:
            self.root.after(700, restart_program)

    def on_close(self) -> None:
        self._shutting_down = True
        self._belt_restart_after_stop = False
        self.app_telemetry.stop()
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None
            self.save_settings(interactive=False, update_detail=False)
        for after_id in (
            self._log_refresh_after_id,
            self._dashboard_timer_after_id,
            self._storage_after_id,
            self._update_poll_after_id,
            self._belt_poll_after_id,
            self._options_scrollregion_after_id,
            self._macos_repaint_after_id,
        ):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        if self.stop_event is not None:
            self.stop_event.set()
        if self.belt_stop_event is not None:
            self._belt_stop_reason = "close"
            self.belt_stop_event.set()
        self._terminate_belt_process()
        self._maybe_close_device_capture_session(force=True)
        self.belt_overlay.close()
        self.hide_droid_timers()
        self.destroy_region_overlay_windows()
        self.root.destroy()


def run_gui() -> None:
    # DPI awareness must be set before the first window exists, or Windows
    # bitmap-scales the UI and fixed sizes stop matching font metrics.
    set_dpi_awareness()
    config = load_config()
    root = make_root(config.ui_theme)
    DroidAlertsApp(root, config=config)
    root.mainloop()
