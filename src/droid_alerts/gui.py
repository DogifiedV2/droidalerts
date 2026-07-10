from __future__ import annotations

import json
import os
import csv
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, DoubleVar, IntVar, StringVar, filedialog, messagebox

import tkinter as tk

try:
    import ttkbootstrap as ttk

    BOOTSTRAP = True
except Exception:
    from tkinter import ttk

    BOOTSTRAP = False

from . import __version__
from .alerts import AlertPolicy
from .capture import (
    MonitorInfo,
    PixelBox,
    create_capture,
    format_monitor_label,
    list_monitors,
    set_dpi_awareness,
)
from .classifier import Detection
from .config import AppConfig, config_dir, load_config, project_root, save_config, user_sounds_dir
from .diagnostics import create_support_bundle
from .logging_io import alert_samples_dir, debug_dir, logs_dir
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
from .game_status import is_game_running
from .timers import format_countdown, seconds_until_next
from .watcher import run_watch


ALERT_COMBOS: tuple[tuple[str, str], ...] = (
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Mythic"),
)
UPDATE_POLL_INTERVAL_MS = 15 * 60 * 1000


def bootstyle(value: str) -> dict[str, str]:
    return {"bootstyle": value} if BOOTSTRAP else {}


def muted_style() -> dict[str, str]:
    return {"style": "Muted.TLabel"}


def make_root() -> tk.Tk:
    if BOOTSTRAP:
        return ttk.Window(themename="darkly")
    return tk.Tk()


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


class DroidAlertsApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Droid Alerts")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.config = load_config()
        self._font_families = self._resolve_font_families()
        self.app_icon: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        self._load_app_icon()
        self.watch_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self._watch_stop_reason = ""
        self.region_overlay: tk.Toplevel | None = None
        self.region_overlay_windows: list[tk.Toplevel] = []
        self.region_positioner: tk.Toplevel | None = None
        self.droid_timers = None
        self.region_box: PixelBox | None = None
        self.region_source: str = ""
        self.region_screen_size: tuple[int, int] | None = None
        self.region_monitor_offset: tuple[int, int] = (0, 0)
        self.update_check_running = False
        self.available_update: dict[str, str] | None = None
        self._update_poll_after_id: str | None = None
        self._log_file_signature: tuple[int, int] | None = None
        self._log_refresh_after_id: str | None = None
        self._autosave_after_id: str | None = None
        self._loading_settings = False
        self._autosave_ready = False
        self.share_debug_detections_check = None
        self.session_detection_count = 0
        self.session_alert_count = 0
        self.session_monitoring_seconds = 0.0
        self._watch_segment_started: float | None = None
        self.last_game_running: bool | None = None
        self._game_check_running = False
        self._game_check_after_id: str | None = None
        self._dashboard_timer_after_id: str | None = None
        self._storage_after_id: str | None = None
        self._resume_when_game_opens = False
        self.history_rows_by_item: dict[str, dict[str, object]] = {}
        self._last_cleanup_at = 0.0

        self.status_var = StringVar(value="Stopped")
        self.detail_var = StringVar(value=f"Config: {config_dir() / 'config.json'}")
        self.region_status_var = StringVar(value="")
        self.watcher_status_var = StringVar(value="Ready to watch")
        self.watcher_detail_var = StringVar(value="Choose the display with Fortnite, then start watching.")
        self.last_scan_var = StringVar(value="No scans yet")
        self.last_alert_var = StringVar(value="No priority alerts this session")
        self.session_stats_var = StringVar(value="0 detections · 0 alerts")
        self.game_status_var = StringVar(value="Fortnite status unavailable")
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
        self.monitor_indexes_by_label: dict[str, int] = {}
        self.options_outer = None
        self.options_canvas: tk.Canvas | None = None
        self.options_canvas_window: int | None = None
        self.options_scrollbar = None
        self._options_content_width: int | None = None
        self._options_scrollregion_bounds: tuple[int, int, int, int] | None = None
        self._options_scrollregion_after_id: str | None = None
        self._macos_repaint_after_id: str | None = None

        self._build_ui()
        self.load_settings()
        self._apply_initial_geometry()
        self._wire_auto_save()
        if self.config.droid_timers_enabled:
            self.show_droid_timers()
        self.refresh_logs()
        self._schedule_log_refresh()
        self.root.after(700, self.run_first_time_intro)
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
        usable_width = max(720, self.root.winfo_screenwidth() - 80)
        usable_height = max(560, self.root.winfo_screenheight() - 140)
        width = min(max(1120, required_width), usable_width)
        height = min(max(760, required_height), usable_height)
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
        if not path.exists():
            return
        try:
            self.app_icon = tk.PhotoImage(file=str(path))
            self.root.iconphoto(True, self.app_icon)
            max_dim = max(self.app_icon.width(), self.app_icon.height())
            factor = max(1, (max_dim + 47) // 48)
            self.header_icon = self.app_icon.subsample(factor, factor)
        except Exception as exc:
            print(f"[GUI] Failed to load app icon: {exc}")

    def _build_ui(self) -> None:
        try:
            ttk.Style().configure("Muted.TLabel", foreground="#aab3c2")
        except Exception:
            pass
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(2, weight=1)
        if self.header_icon is not None:
            ttk.Label(header, image=self.header_icon).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
        title_column = 1 if self.header_icon is not None else 0
        ttk.Label(header, text="DROID ALERTS", font=self._font(20, "bold")).grid(
            row=0, column=title_column, rowspan=2, sticky="w"
        )
        self.header_status_label = ttk.Label(
            header,
            textvariable=self.status_var,
            font=self._font(11, "bold"),
            padding=(14, 7),
            **bootstyle("danger-inverse"),
        )
        self.header_status_label.grid(row=0, column=3, rowspan=2, padx=(12, 10))
        self._apply_watcher_status_style("Stopped")
        self.update_ready_button = ttk.Button(
            header,
            text="Update ready!",
            command=self.show_available_update,
            width=13,
            **bootstyle("success"),
        )
        self.update_ready_button.grid(row=0, column=4, rowspan=2, padx=(0, 10))
        self.update_ready_button.grid_remove()
        ttk.Label(header, text=f"v{__version__}", **muted_style()).grid(
            row=0, column=5, rowspan=2, sticky="e"
        )

        def style_tabs() -> None:
            try:
                style = ttk.Style()
                style.configure("TNotebook.Tab", font=self._font(11, "bold"), padding=(32, 12))
                style.configure("TNotebook", borderwidth=0)
            except Exception:
                pass

        style_tabs()
        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.root.after_idle(style_tabs)

        self.dashboard_tab = ttk.Frame(self.notebook, padding=14)
        self.logs_tab = ttk.Frame(self.notebook, padding=14)
        self.files_tab = ttk.Frame(self.notebook, padding=14)
        self.settings_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.logs_tab, text="History")
        self.notebook.add(self.files_tab, text="Diagnostics")
        self.notebook.add(self.settings_tab, text="Settings")

        self._build_dashboard_tab()
        self._build_logs_tab()
        self._build_files_tab()
        self._build_settings_tab()
        self._wire_macos_repaint_workaround()

        footer = ttk.Frame(outer, padding=(4, 8, 4, 0))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.detail_var, anchor="w", **muted_style()).grid(
            row=0, column=0, sticky="ew"
        )

    def _labeled_section(self, parent, text: str):
        # Accent rail instead of a border box. Drawn with a ttk Separator:
        # plain frames with a bg color never paint on macOS Tk (Aqua), only
        # theme-engine elements render reliably.
        outer = ttk.Frame(parent)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        ttk.Separator(outer, orient="vertical", **bootstyle("info")).grid(
            row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14)
        )
        ttk.Label(outer, text=text.upper(), font=self._font(11, "bold"), **bootstyle("info")).grid(
            row=0, column=1, sticky="w"
        )
        inner = ttk.Frame(outer, padding=(0, 8, 0, 0))
        inner.grid(row=1, column=1, sticky="nsew")
        return outer, inner

    def _link_label(self, parent, text: str, command) -> "ttk.Label":
        label = ttk.Label(parent, text=text, cursor="hand2", **bootstyle("info"))
        label.bind("<Button-1>", lambda _event: command())
        return label

    def _build_dashboard_tab(self) -> None:
        page = self.dashboard_tab
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2)
        page.rowconfigure(1, weight=1)

        hero_outer, hero = self._labeled_section(page, "MONITORING")
        hero_outer.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 24))
        hero.columnconfigure(0, weight=1)
        ttk.Label(hero, textvariable=self.watcher_status_var, font=self._font(18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        watcher_detail = ttk.Label(
            hero,
            textvariable=self.watcher_detail_var,
            wraplength=650,
            justify="left",
            **muted_style(),
        )
        watcher_detail.grid(row=1, column=0, sticky="w", pady=(3, 2))
        ttk.Label(hero, textvariable=self.game_status_var, **muted_style()).grid(
            row=2, column=0, sticky="w"
        )
        self.watch_button = ttk.Button(
            hero,
            text="Start Watching",
            width=19,
            command=self.toggle_watcher,
            **bootstyle("success"),
        )
        self.watch_button.grid(row=0, column=1, rowspan=3, sticky="e", padx=(18, 0))
        self._autowrap(watcher_detail, hero, pad=240)

        display_row = ttk.Frame(hero)
        display_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        display_row.columnconfigure(1, weight=1)
        ttk.Label(display_row, text="Game display", font=self._font(10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.monitor_combobox = ttk.Combobox(
            display_row,
            textvariable=self.monitor_display_var,
            state="readonly",
            width=44,
            postcommand=self.refresh_monitor_choices,
        )
        self.monitor_combobox.grid(row=0, column=1, sticky="ew")
        self.monitor_combobox.bind("<<ComboboxSelected>>", self.on_monitor_selected)
        ttk.Button(
            display_row,
            text="Identify Displays",
            command=self.identify_displays,
            **bootstyle("info-outline"),
        ).grid(row=0, column=2, padx=(10, 0))
        self.refresh_monitor_choices()

        alerts_panel = ttk.Frame(page)
        alerts_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 32))
        alerts_panel.columnconfigure(0, weight=1)
        alerts_panel.rowconfigure(1, weight=1)
        self._build_priority_alerts(alerts_panel, row=0)
        self._build_alert_channels(alerts_panel, row=1)

        right_panel = ttk.Frame(page)
        right_panel.grid(row=1, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)

        glance_outer, glance = self._labeled_section(right_panel, "NEXT SPAWNS")
        glance_outer.grid(row=0, column=0, sticky="new", pady=(0, 24))
        glance.columnconfigure(0, weight=1)
        timer_labels = (("beskar", "Beskar"), ("mythic", "Mythic"), ("rainbow", "Rainbow"))
        for row, (key, label) in enumerate(timer_labels, start=1):
            ttk.Label(glance, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(glance, textvariable=self.timer_vars[key], font=self._font(12, "bold", mono=True)).grid(
                row=row, column=1, sticky="e", pady=3
            )
        self.setting_vars["droid_timers_enabled"] = BooleanVar(value=False)
        ttk.Checkbutton(
            glance,
            text="Show Droid Timers overlay",
            variable=self.setting_vars["droid_timers_enabled"],
            command=self.on_droid_timers_toggle,
            **bootstyle("round-toggle"),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))

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
        alerts_outer.grid(row=row, column=0, sticky="ew", pady=(0, 24))
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
                self._link_label(channels, "Set up", setup).grid(
                    row=row, column=2, sticky="e", padx=(0, 16)
                )
            self._link_label(
                channels, "Test", lambda selected=channel: self.send_channel_test(selected)
            ).grid(row=row, column=3, sticky="e")

    def _build_alert_appearance(self, parent, *, row: int) -> None:
        appearance_outer, appearance = self._labeled_section(parent, "ALERT APPEARANCE")
        appearance_outer.grid(row=row, column=0, sticky="ew", pady=(0, 26))
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

    def _build_logs_tab(self) -> None:
        page = self.logs_tab
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        filters = ttk.Frame(page)
        filters.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        filters.columnconfigure(4, weight=1)
        self.history_filter_var = StringVar(value="All")
        self.history_search_var = StringVar(value="")
        ttk.Label(filters, text="Show").grid(row=0, column=0, padx=(0, 6))
        filter_box = ttk.Combobox(
            filters,
            textvariable=self.history_filter_var,
            values=("All", "Priority alerts", "Detections", "Delivery failures", "Debug"),
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
        widths = {"time": 170, "type": 130, "droid": 110, "rarity": 110, "status": 110, "info": 360}
        for column in columns:
            anchor = "center" if column in {"type", "status"} else "w"
            self.logs_tree.heading(column, text=headings[column], anchor=anchor)
            self.logs_tree.column(column, width=widths[column], anchor=anchor)
        self.logs_tree.tag_configure("success", foreground="#5ce08a")
        self.logs_tree.tag_configure("failure", foreground="#ff6b78")
        self.logs_tree.tag_configure("priority", foreground="#ff65b5")
        self.logs_tree.tag_configure("muted", foreground="#8f97a6")
        self.logs_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=self.logs_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.logs_tree.configure(yscrollcommand=scrollbar.set)
        self.logs_tree.bind("<Double-1>", self.show_history_details)

        actions = ttk.Frame(page)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=self.refresh_logs).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Export CSV", command=self.export_history_csv).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Open Logs Folder", command=lambda: self.open_path(logs_dir())).grid(row=0, column=2)

    def _build_files_tab(self) -> None:
        page = self.files_tab
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)

        setup_outer, setup = self._labeled_section(page, "CHAT REGION")
        setup_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 32))
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
        ttk.Button(setup, text="Move Chat Box…", command=self.open_region_positioner, width=28).grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Button(setup, text="Auto Detect Region", command=self.auto_detect_region, width=28).grid(
            row=3, column=0, sticky="w", pady=4
        )
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
        tools_outer.grid(row=0, column=1, sticky="nsew")
        tools.columnconfigure(0, weight=1)
        storage_label = ttk.Label(tools, textvariable=self.storage_status_var, wraplength=420, justify="left")
        storage_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self._autowrap(storage_label, tools)
        buttons = (
            ("Create Support Bundle", self.create_diagnostics_bundle, "info"),
            ("Check For Updates", lambda: self.check_updates(manual=True), "secondary"),
            ("Open Data Folder", lambda: self.open_path(project_root() / "data"), "secondary"),
            ("Open Alert Samples", lambda: self.open_path(alert_samples_dir()), "secondary"),
            ("Open Debug Screenshots", lambda: self.open_path(debug_dir()), "secondary"),
            ("Clear Debug Screenshots", self.clear_debug_data, "danger-outline"),
            ("Clear History", self.clear_history_data, "danger-outline"),
        )
        for row, (label, command, style) in enumerate(buttons, start=1):
            ttk.Button(tools, text=label, command=command, width=27, **bootstyle(style)).grid(
                row=row, column=0, sticky="w", pady=4
            )

    def _build_settings_tab(self) -> None:
        page = self.settings_tab
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.options_outer = ttk.Frame(page)
        self.options_outer.grid(row=0, column=0, sticky="nsew")
        self.options_outer.columnconfigure(0, weight=1)
        self.options_outer.rowconfigure(0, weight=1)
        try:
            canvas_background = ttk.Style().lookup("TFrame", "background") or self.root.cget("background")
        except Exception:
            canvas_background = self.root.cget("background")
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
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Settings", font=self._font(18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            top,
            text="Advanced settings",
            variable=self.setting_vars["advanced_mode"],
            command=self.on_advanced_toggle,
            **bootstyle("round-toggle"),
        ).grid(row=0, column=2, sticky="e")

        behavior_outer, behavior = self._labeled_section(content, "EVERYDAY BEHAVIOUR")
        behavior_outer.grid(row=1, column=0, sticky="ew", pady=(0, 26))
        behavior.columnconfigure(0, weight=1)
        basic_settings = (
            ("timer_reminders_enabled", "Timer reminder sound", None),
            ("extra_checks", "Improve detection with HDR / washed-out colours", None),
            ("start_watcher_on_launch", "Start watching when Droid Alerts opens", None),
            ("update_check_enabled", "Check for updates automatically", None),
        )
        for key, _label, _command in basic_settings:
            self.setting_vars[key] = BooleanVar(value=False)
        for row, (key, label, command) in enumerate(basic_settings):
            ttk.Checkbutton(
                behavior,
                text=label,
                variable=self.setting_vars[key],
                command=command,
                **bootstyle("round-toggle"),
            ).grid(row=row, column=0, sticky="w", pady=5)
        actions = ttk.Frame(behavior)
        actions.grid(row=0, column=1, rowspan=len(basic_settings), sticky="ne", padx=(24, 0))
        ttk.Button(actions, text="Adjust Timers", command=self.adjust_droid_timers).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(actions, text="What is shared?", command=self.show_privacy_details).grid(row=1, column=0, sticky="ew")

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
            "pause_when_game_closed",
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
        detector_outer.grid(row=1, column=0, sticky="ew", pady=(0, 26))
        detector.columnconfigure(1, weight=1)
        detector_fields = (
            ("Capture interval (seconds)", "capture_interval_seconds"),
            ("Duplicate window (seconds)", "dedupe_seconds"),
            ("Alert cooldown (seconds)", "alert_cooldown_seconds"),
            ("Calibration warning frames", "validation_failures_before_calibration_prompt"),
            ("Timer reminder (seconds before)", "timer_reminder_seconds"),
            ("Timer schedule offset (seconds)", "timer_offset_seconds"),
        )
        detector_ranges = {
            "capture_interval_seconds": (0.05, 5.0, 0.05),
            "dedupe_seconds": (0.0, 300.0, 1.0),
            "alert_cooldown_seconds": (0.0, 300.0, 1.0),
            "validation_failures_before_calibration_prompt": (1, 1000, 1),
            "timer_reminder_seconds": (1, 600, 5),
            "timer_offset_seconds": (-3600, 3600, 1),
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
        ttk.Checkbutton(detector, text="Pause watcher when Fortnite closes", variable=self.setting_vars["pause_when_game_closed"], **bootstyle("round-toggle")).grid(
            row=0, column=2, sticky="w", pady=4
        )
        data_outer, data = self._labeled_section(content, "STORAGE & DEBUG")
        data_outer.grid(row=2, column=0, sticky="ew", pady=(0, 26))
        data.columnconfigure(1, weight=1)
        ttk.Checkbutton(data, text="Save alert screenshots", variable=self.setting_vars["save_alert_samples"], **bootstyle("round-toggle")).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Checkbutton(
            data,
            text="Debug mode (Windows: numpad + snapshot; macOS: every 5 seconds)",
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
        ttk.Label(data, text="Delete captures older than (days; 0 keeps all)").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(
            data,
            textvariable=self.setting_vars["retention_days"],
            values=(0, 1, 7, 30, 90),
            state="readonly",
            width=10,
        ).grid(row=3, column=1, sticky="w", padx=(12, 0))
        ttk.Label(data, text="Maximum runtime storage (MB; 0 unlimited)").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(
            data,
            textvariable=self.setting_vars["max_storage_mb"],
            values=(0, 100, 250, 500, 1000, 2000),
            state="readonly",
            width=10,
        ).grid(row=4, column=1, sticky="w", padx=(12, 0))

        remote_outer, remote = self._labeled_section(self.advanced_container, "NOTIFICATION DETAILS")
        remote_outer.grid(row=2, column=0, sticky="ew", pady=(0, 26))
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
        ttk.Checkbutton(remote, text="Attach screenshot to ntfy", variable=self.setting_vars["ntfy_include_attachment"], **bootstyle("round-toggle")).grid(
            row=0, column=2, sticky="w", pady=4
        )
        ttk.Checkbutton(remote, text="Attach screenshot to Pushover", variable=self.setting_vars["phone_include_attachment"], **bootstyle("round-toggle")).grid(
            row=1, column=2, sticky="w", pady=4
        )
        ttk.Button(remote, text="Open Config", command=lambda: self.open_path(config_dir() / "config.json")).grid(
            row=5, column=2, sticky="e"
        )
        self.root.bind_all("<MouseWheel>", self._on_options_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_options_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_options_mousewheel, add="+")

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
        # The settings tab hosts its content in a Canvas, whose damage
        # tracking needs an explicit nudge on top of the window-level one.
        if selected is self.settings_tab and self.options_canvas is not None:
            try:
                self.options_canvas.configure(background=self.options_canvas.cget("background"))
                self.options_canvas.yview_scroll(0, "units")
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

    def _apply_debug_share_visibility(self, debug_enabled: bool) -> None:
        if self.share_debug_detections_check is None:
            return
        if debug_enabled:
            self.share_debug_detections_check.grid()
        else:
            self.share_debug_detections_check.grid_remove()

    def on_droid_timers_toggle(self) -> None:
        if bool(self._value("droid_timers_enabled")):
            self.show_droid_timers()
        else:
            self.hide_droid_timers()

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
                reminders_enabled=config.timer_reminders_enabled,
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
                "pause_when_game_closed",
                "timer_reminders_enabled",
            ):
                var = self.setting_vars.get(key)
                if isinstance(var, BooleanVar):
                    var.set(bool(getattr(self.config, key)))
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
            if not self.config.save_debug_screenshots:
                self._set_var("share_debug_detections", False)
            self._set_var("advanced_mode", self.config.advanced_mode)
            self._apply_debug_share_visibility(self.config.save_debug_screenshots)
            self._apply_advanced_visibility(self.config.advanced_mode)
            self.refresh_sound_choices()
            self.refresh_channel_statuses()
            self.detail_var.set("Settings loaded")
        finally:
            self._loading_settings = False

    def refresh_monitor_choices(self) -> None:
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

    def on_monitor_selected(self, _event=None) -> None:
        label = self.monitor_display_var.get()
        monitor_index = self.monitor_indexes_by_label.get(label)
        if monitor_index is None:
            return
        self._set_var("monitor_index", monitor_index)
        self.save_settings(interactive=False, update_detail=False)
        if self.droid_timers is not None:
            self.hide_droid_timers()
            self.show_droid_timers()
        self.detail_var.set(f"{label} selected and applied")

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

    def identify_displays(self) -> None:
        try:
            monitors = list_monitors()
        except Exception as exc:
            messagebox.showerror("Identify Displays", f"Displays could not be read:\n{exc}")
            return
        if not monitors:
            messagebox.showinfo("Identify Displays", "No displays were found.")
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
            window.geometry(f"{width}x{height}{x:+d}{y:+d}")
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
            messagebox.showerror("Alert Sound", "Droid Alerts currently supports WAV files.")
            return
        try:
            folder = user_sounds_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source_path.name
            if source_path.resolve() != target.resolve():
                shutil.copy2(source_path, target)
        except OSError as exc:
            messagebox.showerror("Alert Sound", str(exc))
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
                "Droid Alerts always sends a small anonymous watcher heartbeat and priority-alert count using random install and session IDs.",
                "That data includes the app version and detected droid/rarity, but never chat text, player or machine names, credentials, or screenshots.",
                "Screenshots stay on this PC unless a notification attachment or the separate debug-sharing option is explicitly enabled.",
                "Support bundles redact notification topics and never include webhook or API credentials.",
            ),
            ok_text="Close",
            cancel_text="",
        )

    def is_watching(self) -> bool:
        return self.watch_thread is not None and self.watch_thread.is_alive()

    def toggle_watcher(self) -> None:
        if self.is_watching():
            self.stop_watcher()
        elif self._resume_when_game_opens:
            self._resume_when_game_opens = False
            self._set_watcher_state("Stopped")
            self.watcher_status_var.set("Ready to watch")
            self.watcher_detail_var.set("Automatic start cancelled.")
            self.detail_var.set("Automatic watcher start cancelled")
        else:
            self.start_watcher()

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
                self._set_var("monitor_index", int(monitor_index))
                self.refresh_monitor_choices()
            width = event.get("screen_width", "?")
            height = event.get("screen_height", "?")
            source = event.get("region_source", "automatic")
            self.watcher_status_var.set("Watching for priority spawns")
            self.watcher_detail_var.set(f"{self.monitor_display_var.get()} · {width} × {height} · Region: {source}")
            self._set_watcher_state("Running")
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
        if event_type in {"alert", "detection", "delivery"}:
            self.refresh_logs(update_detail=False)

    def _set_watcher_state(self, state: str) -> None:
        self.status_var.set(state)
        self._apply_watcher_status_style(state)
        if state in {"Running", "Warning"} or self.is_watching():
            self.watch_button.configure(text="Stop Watching", state="normal", **bootstyle("danger"))
        elif state == "Paused" and self._resume_when_game_opens:
            self.watch_button.configure(text="Cancel Auto-start", state="normal", **bootstyle("warning"))
        else:
            self.watch_button.configure(text="Start Watching", state="normal", **bootstyle("success"))

    def _apply_watcher_status_style(self, state: str) -> None:
        style_name, color = {
            "Running": ("success-inverse", "#36c96b"),
            "Paused": ("warning-inverse", "#f0ad4e"),
            "Warning": ("warning-inverse", "#f0ad4e"),
            "Stopped": ("danger-inverse", "#e85d68"),
            "Error": ("danger-inverse", "#e85d68"),
        }.get(state, ("secondary-inverse", "#aab3c2"))
        if BOOTSTRAP:
            self.header_status_label.configure(bootstyle=style_name)
            return
        fallback_style = "WatcherStatus.TLabel"
        ttk.Style().configure(fallback_style, foreground=color)
        self.header_status_label.configure(style=fallback_style)

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
        self._check_game_status(initial=True)

    def _schedule_game_check(self) -> None:
        self._game_check_after_id = self.root.after(5000, self._check_game_status)

    def _check_game_status(self, *, initial: bool = False) -> None:
        if sys.platform != "win32":
            self._game_status_ready(None, initial)
            return
        if self._game_check_running:
            return
        self._game_check_running = True

        def worker() -> None:
            game = is_game_running()
            self._post_to_ui(lambda: self._game_status_ready(game, initial))

        threading.Thread(target=worker, name="DroidAlertsGameStatus", daemon=True).start()

    def _game_status_ready(self, game: bool | None, initial: bool) -> None:
        self._game_check_running = False
        self.last_game_running = game
        self._update_game_status_text(game)
        config = self.config
        if initial and config.start_watcher_on_launch:
            if config.pause_when_game_closed and game is False:
                self._resume_when_game_opens = True
                self.watcher_status_var.set("Paused until Fortnite opens")
                self._set_watcher_state("Paused")
            else:
                self.start_watcher()
        elif config.pause_when_game_closed:
            if game is False and self.is_watching():
                self._resume_when_game_opens = True
                self.stop_watcher(reason="game_closed")
            elif game is True and self._resume_when_game_opens and not self.is_watching():
                self._resume_when_game_opens = False
                self.start_watcher()
        if sys.platform == "win32":
            self._schedule_game_check()

    def _update_game_status_text(self, game: bool | None) -> None:
        if game is True:
            self.game_status_var.set("Fortnite is running")
        elif game is False:
            self.game_status_var.set("Fortnite is not running")
        else:
            self.game_status_var.set("Fortnite status is available in the Windows build")

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
        """First-launch walkthrough: region check in Diagnostics, then the
        Droid Timers question, then phone alerts. One time only."""
        config = load_config()
        # Existing installs (already past the phone prompt) skip the intro.
        if config.intro_shown or config.notification_setup_prompted:
            if not config.intro_shown:
                config.intro_shown = True
                save_config(config)
                self.config = config
            self.prompt_notification_setup_if_needed()
            return

        try:
            self.notebook.select(self.files_tab)
        except Exception:
            pass

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
        if interactive and not selected and not messagebox.askyesno(
            "No Priority Alerts", "Continue with no priority alerts selected?"
        ):
            return None

        config = load_config()
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
        except (TypeError, ValueError, tk.TclError) as exc:
            if interactive:
                messagebox.showerror("Settings", f"Invalid numeric setting: {exc}")
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
        config.pause_when_game_closed = bool(self._value("pause_when_game_closed"))
        config.timer_reminders_enabled = bool(self._value("timer_reminders_enabled"))
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
        ok_text: str = "Continue",
        cancel_text: str = "Cancel",
        modal: bool = True,
    ) -> dict[str, str] | None:
        """Styled dialog for setup flows.

        Replaces messagebox/simpledialog so tutorials read as plain steps
        instead of a chain of bare OS prompts with generic icons. Returns the
        entered field values ({} when there are no fields) or None on cancel.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        result: dict[str, str] | None = None

        body = ttk.Frame(dialog, padding=(22, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        wrap = 460
        row = 0

        ttk.Label(body, text=title, font=self._font(14, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 10)
        )
        row += 1
        if intro:
            ttk.Label(body, text=intro, wraplength=wrap, justify="left").grid(
                row=row, column=0, sticky="w", pady=(0, 10)
            )
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
        ttk.Button(buttons, text=ok_text, command=accept, **bootstyle("success")).grid(
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
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
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
        existing, _source = load_phone_alert_credentials(config)
        token = (existing or {}).get("token", "")
        user = (existing or {}).get("user", "")
        error = ""
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
            policy.notify(detection)
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
            credentials, _source = load_phone_alert_credentials(config)
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
        game = self.last_game_running if config.pause_when_game_closed else None
        if config.pause_when_game_closed and game is False:
            self._resume_when_game_opens = True
            self._set_watcher_state("Paused")
            self.watcher_status_var.set("Paused until Fortnite opens")
            self.watcher_detail_var.set("Monitoring will start automatically when the game starts.")
            self.detail_var.set("Waiting for Fortnite before starting the watcher")
            return
        self.stop_event = threading.Event()
        self._watch_stop_reason = ""
        self._watch_segment_started = time.monotonic()
        self.watch_thread = threading.Thread(target=self._watch_thread, args=(config, self.stop_event), daemon=True)
        self.watch_thread.start()
        self._set_watcher_state("Running")
        self.watcher_status_var.set("Starting screen capture…")
        self.watcher_detail_var.set(f"Preparing {self.monitor_display_var.get()}")
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
            if reason == "game_closed":
                self._set_watcher_state("Paused")
                self.watcher_status_var.set("Paused until Fortnite opens")
                self.watcher_detail_var.set("Monitoring will resume automatically when the game starts.")
                self.detail_var.set("Watcher paused because Fortnite closed")
            else:
                self._set_watcher_state("Stopped")
                self.watcher_status_var.set("Ready to watch")
                self.watcher_detail_var.set("Choose the display with Fortnite, then start watching.")
                self.detail_var.set("Watcher stopped")
        else:
            self._set_watcher_state("Error")
            self.watcher_status_var.set("Monitoring stopped unexpectedly")
            self.watcher_detail_var.set(str(exc))
            self.detail_var.set(f"Watcher stopped: {exc}")
            messagebox.showerror("Watcher", str(exc))
        self._watch_stop_reason = ""

    def stop_watcher(self, *, reason: str = "manual") -> None:
        if self.stop_event is not None:
            self._watch_stop_reason = reason
            if reason == "manual":
                self._resume_when_game_opens = False
            self.stop_event.set()
            self.watch_button.configure(text="Stopping…", state="disabled")
            self.detail_var.set("Pausing watcher…" if reason == "game_closed" else "Stopping watcher…")
        else:
            self.detail_var.set("Watcher is not running")

    def _schedule_log_refresh(self) -> None:
        self._log_refresh_after_id = self.root.after(2000, self._auto_refresh_logs)

    def _auto_refresh_logs(self) -> None:
        self.refresh_logs(update_detail=False, only_if_changed=True)
        self._schedule_log_refresh()

    def refresh_logs(self, *, update_detail: bool = True, only_if_changed: bool = False) -> None:
        path = logs_dir() / "events.jsonl"
        if not path.exists():
            for item in self.logs_tree.get_children():
                self.logs_tree.delete(item)
            self.history_rows_by_item.clear()
            self.history_summary_var.set("No history yet")
            if update_detail:
                self.detail_var.set("History is empty; detections and delivery results will appear here")
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
        self._log_file_signature = signature

        for item in self.logs_tree.get_children():
            self.logs_tree.delete(item)
        try:
            raw_lines = read_last_lines(path, max_lines=3000)
        except OSError as exc:
            if update_detail:
                self.detail_var.set(f"Could not read logs: {exc}")
            return

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
            event_type = str(row.get("event_type") or "")
            is_debug_row = self._log_row_is_debug(row)
            if selected_filter == "All" and is_debug_row:
                continue
            if selected_filter == "Priority alerts" and not (
                event_type == "alert" or (not event_type and bool(row.get("alerted")))
            ):
                continue
            if selected_filter == "Detections" and event_type not in {"alert", "detected", "seen"}:
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
        text = tk.Text(body, wrap="word", bg="#111827", fg="#e9eef6", insertbackground="white")
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
            messagebox.showinfo("Export History", "There are no visible history rows to export.")
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
            messagebox.showerror("Export History", str(exc))
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
            messagebox.showerror("Open", str(exc))

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
                messagebox.showerror("Move Chat Box", str(exc))
                return
        if self.region_box is None:
            return

        dialog = tk.Toplevel(self.root)
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
        dialog.geometry(f"{x:+d}{y:+d}")
        dialog.focus_force()

    def create_diagnostics_bundle(self) -> None:
        self.detail_var.set("Creating support bundle…")
        config = load_config()

        def worker() -> None:
            try:
                path = create_support_bundle(config)
            except Exception as exc:
                self._post_to_ui(lambda exc=exc: messagebox.showerror("Support Bundle", str(exc)))
                return
            self._post_to_ui(lambda path=path: self._support_bundle_ready(path))

        threading.Thread(target=worker, name="DroidAlertsSupportBundle", daemon=True).start()

    def _support_bundle_ready(self, path: Path) -> None:
        self.detail_var.set(f"Support bundle created: {path.name}")
        self._refresh_storage_status(cleanup=False)
        if messagebox.askyesno("Support Bundle", f"Created a redacted support bundle:\n\n{path}\n\nOpen its folder?"):
            self.open_path(path.parent)

    def clear_debug_data(self) -> None:
        if not messagebox.askyesno("Clear Debug Screenshots", "Delete all locally saved debug screenshots?"):
            return
        result = clear_debug_captures()
        self.detail_var.set(f"Deleted {result.deleted_files} debug file(s), freeing {format_bytes(result.freed_bytes)}")
        self._refresh_storage_status(cleanup=False)

    def clear_history_data(self) -> None:
        if not messagebox.askyesno("Clear History", "Delete all event history? This cannot be undone."):
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
            f"Debug {format_bytes(summary['debug'])}"
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
        capture = create_capture(monitor_index=self.config.monitor_index)
        try:
            screen_w, screen_h = capture.screen_size()
            box, source = RegionResolver(
                screen_w,
                screen_h,
                max_failures=self.config.validation_failures_before_calibration_prompt,
                monitor_key=getattr(getattr(capture, "monitor", None), "key", None),
            ).resolve()
            monitor = getattr(capture, "monitor", None)
            left_offset = int(getattr(monitor, "left", 0))
            top_offset = int(getattr(monitor, "top", 0))
            self.region_box = box
            self.region_source = source
            self.region_screen_size = (screen_w, screen_h)
            self.region_monitor_offset = (left_offset, top_offset)
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
        ).save(getattr(self._current_monitor_info(), "key", None))
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
        Calibration().save(getattr(self._current_monitor_info(), "key", None))
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
            bar.geometry(f"{w}x{h}{x:+d}{y:+d}")
            windows.append(bar)

        add_bar(left, top, width, thickness)
        add_bar(left, top + height - thickness, width, thickness)
        add_bar(left, top, thickness, height)
        add_bar(left + width - thickness, top, thickness, height)

        title = f"Droid Alerts region: {source}"
        label_height = 24
        if top >= label_height + 4:
            label = tk.Toplevel(self.root)
            label.overrideredirect(True)
            label.attributes("-topmost", True)
            label.configure(bg=color)
            label_width = min(width, max(230, min(420, len(title) * 9 + 18)))
            label.geometry(f"{label_width}x{label_height}{left:+d}{top - label_height - 2:+d}")
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
                messagebox.showerror("Updates", str(exc))
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

        self.detail_var.set("Update installed, restarting...")
        if self.stop_event is not None:
            self.stop_event.set()
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
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None
            self.save_settings(interactive=False, update_detail=False)
        for after_id in (
            self._log_refresh_after_id,
            self._game_check_after_id,
            self._dashboard_timer_after_id,
            self._storage_after_id,
            self._update_poll_after_id,
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
        self.hide_droid_timers()
        self.destroy_region_overlay_windows()
        self.root.destroy()


def run_gui() -> None:
    # DPI awareness must be set before the first window exists, or Windows
    # bitmap-scales the UI and fixed sizes stop matching font metrics.
    set_dpi_awareness()
    root = make_root()
    DroidAlertsApp(root)
    root.mainloop()
