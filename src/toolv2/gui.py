from __future__ import annotations

import json
import os
import threading
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import BooleanVar, DoubleVar, IntVar, StringVar, messagebox, simpledialog

import tkinter as tk

try:
    import ttkbootstrap as ttk

    BOOTSTRAP = True
except Exception:
    from tkinter import ttk

    BOOTSTRAP = False

from .alerts import AlertPolicy
from .capture import create_capture, set_dpi_awareness
from .classifier import Detection
from .config import AppConfig, config_dir, data_dir, load_config, project_root, save_config
from .logging_io import alert_samples_dir, debug_dir, logs_dir
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
from .region import RegionResolver
from .watcher import run_watch


ALERT_COMBOS: tuple[tuple[str, str], ...] = (
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Mythic"),
)


def bootstyle(value: str) -> dict[str, str]:
    return {"bootstyle": value} if BOOTSTRAP else {}


def make_root() -> tk.Tk:
    if BOOTSTRAP:
        return ttk.Window(themename="darkly")
    return tk.Tk()


class DroidAlertsApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DroidAlerts")
        self.root.geometry("980x700")
        self.root.minsize(880, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.config = load_config()
        self.app_icon: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        self._load_app_icon()
        self.watch_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.region_overlay: tk.Toplevel | None = None
        self.update_check_running = False
        self._log_file_signature: tuple[int, int] | None = None
        self._log_refresh_after_id: str | None = None

        self.status_var = StringVar(value="Stopped")
        self.detail_var = StringVar(value=f"Config: {config_dir() / 'config.json'}")
        self.setting_vars: dict[str, object] = {}
        self.alert_vars: dict[tuple[str, str], BooleanVar] = {}

        self._build_ui()
        self.load_settings()
        self.refresh_logs()
        self._schedule_log_refresh()
        self.root.after(700, self.prompt_notification_setup_if_needed)
        self.root.after(1500, lambda: self.check_updates(manual=False))

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
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        title_col = 1 if self.header_icon is not None else 0
        status_col = title_col + 1
        first_button_col = status_col + 1
        header.columnconfigure(status_col, weight=1)

        if self.header_icon is not None:
            ttk.Label(header, image=self.header_icon).grid(row=0, column=0, sticky="w", padx=(0, 8))

        ttk.Label(header, text="DroidAlerts", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=title_col, sticky="w"
        )
        ttk.Label(header, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=status_col, sticky="w", padx=16
        )
        self.start_button = ttk.Button(
            header, text="Start", width=12, command=self.start_watcher, **bootstyle("success")
        )
        self.start_button.grid(row=0, column=first_button_col, padx=(0, 8))
        self.stop_button = ttk.Button(
            header, text="Stop", width=12, command=self.stop_watcher, **bootstyle("danger")
        )
        self.stop_button.grid(row=0, column=first_button_col + 1, padx=(0, 8))
        ttk.Button(
            header, text="Test Alert", width=12, command=self.send_test_alert, **bootstyle("warning")
        ).grid(row=0, column=first_button_col + 2)
        ttk.Label(header, textvariable=self.detail_var).grid(
            row=1, column=0, columnspan=first_button_col + 3, sticky="ew", pady=(7, 0)
        )

        notebook = ttk.Notebook(outer)
        notebook.grid(row=1, column=0, sticky="nsew")

        self.settings_tab = ttk.Frame(notebook, padding=12)
        self.runtime_tab = ttk.Frame(notebook, padding=12)
        self.logs_tab = ttk.Frame(notebook, padding=12)
        self.files_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.runtime_tab, text="Runtime")
        notebook.add(self.logs_tab, text="Logs")
        notebook.add(self.files_tab, text="Files")

        self._build_settings_tab()
        self._build_runtime_tab()
        self._build_logs_tab()
        self._build_files_tab()

    def _labeled_section(self, parent, text: str, padding: int = 12):
        outer = ttk.LabelFrame(parent, text=text)
        inner = ttk.Frame(outer, padding=padding)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _add_save_actions(self, parent, *, row: int, columnspan: int = 1) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=columnspan, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Reload", command=self.load_settings).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Save", command=self.save_settings, **bootstyle("success")).grid(
            row=0, column=2
        )

    def _build_settings_tab(self) -> None:
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.columnconfigure(1, weight=1)
        self.settings_tab.rowconfigure(0, weight=1)

        alerts_outer, alerts_frame = self._labeled_section(self.settings_tab, "Priority Alerts")
        alerts_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        alerts_frame.columnconfigure(0, weight=1)
        for row, combo in enumerate(ALERT_COMBOS):
            var = BooleanVar(value=True)
            self.alert_vars[combo] = var
            ttk.Checkbutton(
                alerts_frame,
                text=f"{combo[0]} Droid ({combo[1]})",
                variable=var,
            ).grid(row=row, column=0, sticky="w", pady=3)

        channels_outer, channels_frame = self._labeled_section(self.settings_tab, "Alert Channels")
        channels_outer.grid(row=0, column=1, sticky="nsew")
        channels_frame.columnconfigure(0, weight=1)
        for key, label in (
            ("popup_enabled", "Popup alerts"),
            ("sound_enabled", "Sound alerts"),
            ("save_alert_samples", "Save alert samples"),
            ("ntfy_enabled", "ntfy phone alerts (recommended)"),
            ("discord_enabled", "Discord webhook alerts"),
            ("phone_alerts_enabled", "Pushover phone alerts"),
            ("ntfy_include_attachment", "Attach alert sample to ntfy"),
            ("phone_include_attachment", "Attach alert sample to Pushover"),
            ("update_check_enabled", "Check GitHub releases on startup"),
        ):
            self.setting_vars[key] = BooleanVar(value=False)
        for row, (key, label) in enumerate(
            (
                ("popup_enabled", "Popup alerts"),
                ("sound_enabled", "Sound alerts"),
                ("save_alert_samples", "Save alert samples"),
                ("ntfy_enabled", "ntfy phone alerts (recommended)"),
                ("discord_enabled", "Discord webhook alerts"),
                ("phone_alerts_enabled", "Pushover phone alerts"),
                ("ntfy_include_attachment", "Attach alert sample to ntfy"),
                ("phone_include_attachment", "Attach alert sample to Pushover"),
                ("update_check_enabled", "Check GitHub releases on startup"),
            )
        ):
            command = None
            if key == "ntfy_enabled":
                command = self.on_ntfy_alert_toggle
            elif key == "discord_enabled":
                command = self.on_discord_alert_toggle
            elif key == "phone_alerts_enabled":
                command = self.on_phone_alert_toggle
            ttk.Checkbutton(
                channels_frame,
                text=label,
                variable=self.setting_vars[key],
                command=command,
            ).grid(row=row, column=0, sticky="w", pady=3)

        ttk.Button(
            channels_frame,
            text="Set Up ntfy",
            command=self.setup_ntfy_alerts_and_enable,
            **bootstyle("success-outline"),
        ).grid(row=3, column=1, padx=(12, 0), sticky="ew")
        ttk.Button(
            channels_frame,
            text="Set Up Discord",
            command=self.setup_discord_alerts_and_enable,
            **bootstyle("info-outline"),
        ).grid(row=4, column=1, padx=(12, 0), sticky="ew")
        ttk.Button(
            channels_frame,
            text="Set Up Pushover",
            command=self.setup_phone_alerts_and_enable,
            **bootstyle("warning-outline"),
        ).grid(row=5, column=1, padx=(12, 0), sticky="ew")

        self._add_save_actions(self.settings_tab, row=1, columnspan=2)

    def _build_runtime_tab(self) -> None:
        self.runtime_tab.columnconfigure(0, weight=1)
        self.runtime_tab.rowconfigure(0, weight=1)

        runtime_outer, runtime_frame = self._labeled_section(self.runtime_tab, "Runtime")
        runtime_outer.grid(row=0, column=0, sticky="nsew")
        for column in (1, 3):
            runtime_frame.columnconfigure(column, weight=1)

        self.setting_vars["monitor_index"] = IntVar(value=1)
        self.setting_vars["capture_interval_seconds"] = DoubleVar(value=0.25)
        self.setting_vars["dedupe_seconds"] = DoubleVar(value=12.0)
        self.setting_vars["alert_cooldown_seconds"] = DoubleVar(value=10.0)
        self.setting_vars["validation_failures_before_calibration_prompt"] = IntVar(value=30)
        self.setting_vars["popup_seconds"] = DoubleVar(value=8.0)
        self.setting_vars["ntfy_server_url"] = StringVar(value="https://ntfy.sh")
        self.setting_vars["ntfy_topic"] = StringVar(value="")
        self.setting_vars["ntfy_priority"] = StringVar(value="5")
        self.setting_vars["ntfy_tags"] = StringVar(value="rotating_light")
        self.setting_vars["phone_sound"] = StringVar(value="siren")
        self.setting_vars["update_repo"] = StringVar(value="DogifiedV2/droidalerts")

        fields = (
            ("Monitor index", "monitor_index"),
            ("Capture interval (sec)", "capture_interval_seconds"),
            ("Dedupe window (sec)", "dedupe_seconds"),
            ("Alert cooldown (sec)", "alert_cooldown_seconds"),
            ("Calibration hint frames", "validation_failures_before_calibration_prompt"),
            ("Popup duration (sec)", "popup_seconds"),
            ("ntfy server", "ntfy_server_url"),
            ("ntfy topic", "ntfy_topic"),
            ("ntfy priority", "ntfy_priority"),
            ("ntfy tags", "ntfy_tags"),
            ("Pushover sound", "phone_sound"),
            ("Release repo", "update_repo"),
        )
        for index, (label, key) in enumerate(fields):
            row = index // 2
            column = (index % 2) * 2
            ttk.Label(runtime_frame, text=label).grid(row=row, column=column, sticky="w", pady=5)
            var = self.setting_vars[key]
            entry = ttk.Entry(runtime_frame, textvariable=var, width=28)
            entry.grid(row=row, column=column + 1, sticky="ew", padx=(8, 18), pady=5)

        self._add_save_actions(self.runtime_tab, row=1)

    def _build_logs_tab(self) -> None:
        self.logs_tab.rowconfigure(0, weight=1)
        self.logs_tab.columnconfigure(0, weight=1)
        columns = ("time", "droid", "rarity", "priority", "alerted", "score")
        self.logs_tree = ttk.Treeview(self.logs_tab, columns=columns, show="headings", height=18)
        headings = {
            "time": "Time",
            "droid": "Droid",
            "rarity": "Rarity",
            "priority": "Priority",
            "alerted": "Alerted",
            "score": "Score",
        }
        widths = {"time": 180, "droid": 120, "rarity": 110, "priority": 90, "alerted": 90, "score": 90}
        anchors = {
            "time": "w",
            "droid": "w",
            "rarity": "w",
            "priority": "center",
            "alerted": "center",
            "score": "center",
        }
        for column in columns:
            self.logs_tree.heading(column, text=headings[column], anchor=anchors[column])
            self.logs_tree.column(column, width=widths[column], anchor=anchors[column])
        self.logs_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(self.logs_tab, orient="vertical", command=self.logs_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.logs_tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(self.logs_tab)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="Refresh", command=self.refresh_logs).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Open Logs Folder", command=lambda: self.open_path(logs_dir())).grid(
            row=0, column=1
        )

    def _build_files_tab(self) -> None:
        self.files_tab.columnconfigure(0, weight=1)
        paths_outer, paths_frame = self._labeled_section(self.files_tab, "Files")
        paths_outer.grid(row=0, column=0, sticky="nsew")
        paths_frame.columnconfigure(0, weight=1)

        buttons = (
            ("View Region", self.toggle_region_overlay),
            ("Open Config", lambda: self.open_path(config_dir() / "config.json")),
            ("Open Logs Folder", lambda: self.open_path(logs_dir())),
            ("Open Alert Samples Folder", lambda: self.open_path(alert_samples_dir())),
            ("Open Debug Screenshots Folder", lambda: self.open_path(debug_dir())),
            ("Open Source Folder", lambda: self.open_path(project_root())),
            ("Check For Updates", lambda: self.check_updates(manual=True)),
        )
        for row, (label, command) in enumerate(buttons):
            ttk.Button(paths_frame, text=label, command=command, width=28).grid(
                row=row, column=0, sticky="w", pady=4
            )

    def load_settings(self) -> None:
        self.config = load_config()
        selected = set(self.config.targets)
        for combo, var in self.alert_vars.items():
            var.set(combo in selected)
        for key in (
            "popup_enabled",
            "sound_enabled",
            "save_alert_samples",
            "ntfy_enabled",
            "discord_enabled",
            "phone_alerts_enabled",
            "ntfy_include_attachment",
            "phone_include_attachment",
            "update_check_enabled",
        ):
            var = self.setting_vars.get(key)
            if isinstance(var, BooleanVar):
                var.set(bool(getattr(self.config, key)))
        self._set_var("monitor_index", self.config.monitor_index)
        self._set_var("capture_interval_seconds", self.config.capture_interval_seconds)
        self._set_var("dedupe_seconds", self.config.dedupe_seconds)
        self._set_var("alert_cooldown_seconds", self.config.alert_cooldown_seconds)
        self._set_var(
            "validation_failures_before_calibration_prompt",
            self.config.validation_failures_before_calibration_prompt,
        )
        self._set_var("popup_seconds", self.config.popup_seconds)
        self._set_var("ntfy_server_url", self.config.ntfy_server_url)
        self._set_var("ntfy_topic", self.config.ntfy_topic)
        self._set_var("ntfy_priority", self.config.ntfy_priority)
        self._set_var("ntfy_tags", self.config.ntfy_tags)
        self._set_var("phone_sound", self.config.phone_sound)
        self._set_var("update_repo", self.config.update_repo)
        self.detail_var.set(f"Settings loaded from {config_dir() / 'config.json'}")

    def _set_var(self, key: str, value: object) -> None:
        var = self.setting_vars.get(key)
        if hasattr(var, "set"):
            var.set(value)

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

        if messagebox.askyesno(
            "Set Up Phone Alerts",
            "Set up ntfy phone alerts now?\n\n"
            "ntfy is the recommended option. Install the ntfy app, subscribe to a private topic, "
            "then enter that topic here.",
        ):
            if self.setup_ntfy_alerts_and_enable():
                return

        config = load_config()
        config.notification_setup_prompted = True
        save_config(config)
        self.config = config

    def save_settings(self) -> AppConfig | None:
        selected = [combo for combo, var in self.alert_vars.items() if var.get()]
        if not selected and not messagebox.askyesno(
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
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Settings", f"Invalid numeric setting: {exc}")
            return None

        config.sound_enabled = bool(self._value("sound_enabled"))
        config.popup_enabled = bool(self._value("popup_enabled"))
        config.save_alert_samples = bool(self._value("save_alert_samples"))
        config.ntfy_enabled = bool(self._value("ntfy_enabled"))
        config.discord_enabled = bool(self._value("discord_enabled"))
        config.phone_alerts_enabled = bool(self._value("phone_alerts_enabled"))
        config.ntfy_include_attachment = bool(self._value("ntfy_include_attachment"))
        config.phone_include_attachment = bool(self._value("phone_include_attachment"))
        config.update_check_enabled = bool(self._value("update_check_enabled"))
        config.ntfy_server_url = str(self._value("ntfy_server_url")).strip() or "https://ntfy.sh"
        config.ntfy_topic = str(self._value("ntfy_topic")).strip()
        config.ntfy_priority = str(self._value("ntfy_priority")).strip() or "5"
        config.ntfy_tags = str(self._value("ntfy_tags")).strip() or "rotating_light"
        config.phone_sound = str(self._value("phone_sound")).strip() or "siren"
        config.update_repo = str(self._value("update_repo")).strip() or "DogifiedV2/droidalerts"
        config.alert_targets = [list(combo) for combo in selected]

        if config.ntfy_enabled and not ntfy_configured(config):
            if not self.setup_ntfy_alerts(config):
                config.ntfy_enabled = False
                self._set_var("ntfy_enabled", False)
        if config.discord_enabled and not discord_webhook_configured(config):
            if not self.setup_discord_alerts(config):
                config.discord_enabled = False
                self._set_var("discord_enabled", False)
        if config.phone_alerts_enabled and not phone_alerts_configured(config):
            if not self.setup_phone_alerts(config):
                config.phone_alerts_enabled = False
                self._set_var("phone_alerts_enabled", False)

        save_config(config)
        self.config = config
        self.detail_var.set("Settings saved")
        return config

    def _value(self, key: str) -> object:
        var = self.setting_vars[key]
        if hasattr(var, "get"):
            return var.get()
        return var

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
        messagebox.showinfo(
            "ntfy Setup",
            "ntfy is the recommended phone alert option.\n\n"
            "Install the ntfy app, subscribe to a hard-to-guess topic, then enter the same topic here.",
        )
        server_url = simpledialog.askstring(
            "ntfy",
            "ntfy server URL:",
            initialvalue=config.ntfy_server_url or "https://ntfy.sh",
            parent=self.root,
        )
        if server_url is None:
            return False
        server_url = server_url.strip().rstrip("/") or "https://ntfy.sh"
        if not valid_ntfy_server_url(server_url):
            messagebox.showerror("ntfy", "Server URL must start with http:// or https://.")
            return False

        topic = simpledialog.askstring(
            "ntfy",
            "Topic name (letters, numbers, _ and - only):",
            initialvalue=config.ntfy_topic,
            parent=self.root,
        )
        if topic is None:
            return False
        topic = topic.strip()
        if not valid_ntfy_topic(topic):
            messagebox.showerror(
                "ntfy",
                "Topic must be 1-64 chars and only use letters, numbers, _ or -.",
            )
            return False

        current_token, _source = load_ntfy_token(config)
        token = simpledialog.askstring(
            "ntfy",
            "Bearer token (optional; leave blank for public topics):",
            initialvalue=current_token or "",
            show="*",
            parent=self.root,
        )
        if token is None:
            return False

        config.ntfy_server_url = server_url
        config.ntfy_topic = topic
        config.ntfy_priority = str(config.ntfy_priority or "5")
        config.ntfy_tags = str(config.ntfy_tags or "rotating_light")
        save_ntfy_token(config, token)
        self.detail_var.set(f"ntfy topic saved: {server_url}/{topic}")
        try:
            send_ntfy_test_alert(config)
        except Exception as exc:
            messagebox.showerror("ntfy", f"The ntfy test alert failed.\n\n{exc}")
            return False
        if not messagebox.askyesno("ntfy", "A test alert was sent. Did it arrive?"):
            return False
        return True

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
        webhook_url = simpledialog.askstring(
            "Discord Webhook",
            "Paste the Discord webhook URL:",
            initialvalue=current or "",
            parent=self.root,
        )
        if webhook_url is None:
            return False
        webhook_url = webhook_url.strip().lstrip("\ufeff")
        if not valid_discord_webhook_url(webhook_url):
            messagebox.showerror("Discord Webhook", "That does not look like a Discord webhook URL.")
            return False
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
        token = simpledialog.askstring(
            "Pushover",
            "Paste the Pushover app token:",
            parent=self.root,
        )
        if token is None:
            return False
        user = simpledialog.askstring(
            "Pushover",
            "Paste the Pushover user key:",
            parent=self.root,
        )
        if user is None:
            return False
        token = token.strip()
        user = user.strip()
        if not token or not user:
            messagebox.showerror("Pushover", "Both Pushover values are required.")
            return False
        path = save_phone_credentials(config, token, user)
        self.detail_var.set(f"Pushover credentials saved to {path}")
        credentials = {"token": token, "user": user}
        try:
            send_phone_test_alert(credentials, sound=config.phone_sound)
        except Exception as exc:
            messagebox.showerror(
                "Pushover",
                f"The credentials were saved, but the test alert failed.\n\n{exc}",
            )
            return False
        if not messagebox.askyesno("Pushover", "A test alert was sent. Did it arrive?"):
            return False
        return True

    def on_ntfy_alert_toggle(self) -> None:
        if not bool(self._value("ntfy_enabled")):
            return
        config = load_config()
        config.ntfy_server_url = str(self._value("ntfy_server_url")).strip() or "https://ntfy.sh"
        config.ntfy_topic = str(self._value("ntfy_topic")).strip()
        if not ntfy_configured(config) and not self.setup_ntfy_alerts_and_enable():
            messagebox.showinfo("ntfy", "ntfy alerts were left off because no topic was saved.")

    def on_discord_alert_toggle(self) -> None:
        if not bool(self._value("discord_enabled")):
            return
        if not discord_webhook_configured(load_config()) and not self.setup_discord_alerts_and_enable():
            messagebox.showinfo("Discord", "Discord alerts were left off because no webhook was saved.")

    def on_phone_alert_toggle(self) -> None:
        if not bool(self._value("phone_alerts_enabled")):
            return
        if not phone_alerts_configured(load_config()) and not self.setup_phone_alerts_and_enable():
            messagebox.showinfo("Pushover", "Pushover alerts were left off because no credentials were saved.")

    def send_test_alert(self) -> None:
        config = self.save_settings()
        if config is None:
            return
        detection = Detection(
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
        sent_channels: list[str] = []
        if config.popup_enabled:
            show_popup(
                detection,
                config.popup_seconds,
                icon_path=popup_icon_path(config),
                parent=self.root,
            )
            sent_channels.append("popup")
        if config.sound_enabled:
            AlertPolicy(config).notify(detection)
            sent_channels.append("sound")
        if config.discord_enabled:
            webhook_url, _source = load_discord_webhook(config)
            if webhook_url:
                threading.Thread(target=send_discord_alert, args=(webhook_url, detection), daemon=True).start()
                sent_channels.append("Discord")
        if config.ntfy_enabled and ntfy_configured(config):
            threading.Thread(
                target=send_ntfy_alert,
                args=(config, detection),
                kwargs={"attachment_path": None},
                daemon=True,
            ).start()
            sent_channels.append("ntfy")
        if config.phone_alerts_enabled:
            credentials, _source = load_phone_alert_credentials(config)
            if credentials:
                threading.Thread(
                    target=send_phone_alert,
                    args=(credentials, detection),
                    kwargs={"sound": config.phone_sound, "attachment_path": None},
                    daemon=True,
                ).start()
                sent_channels.append("Pushover")
        if sent_channels:
            self.detail_var.set(f"Test alert sent: {', '.join(sent_channels)} - {event_text(detection)}")
        else:
            self.detail_var.set("No alert channels are enabled")

    def start_watcher(self) -> None:
        if self.watch_thread is not None and self.watch_thread.is_alive():
            self.detail_var.set("Watcher is already running")
            return
        config = self.save_settings()
        if config is None:
            return
        self.stop_event = threading.Event()
        self.watch_thread = threading.Thread(target=self._watch_thread, args=(config, self.stop_event), daemon=True)
        self.watch_thread.start()
        self.status_var.set("Running")
        self.detail_var.set("Watcher started")

    def _watch_thread(self, config: AppConfig, stop_event: threading.Event) -> None:
        try:
            run_watch(debug=False, config=config, stop_event=stop_event, popup_parent=self.root)
            self.root.after(0, lambda: self._watcher_finished(None))
        except Exception as exc:
            self.root.after(0, lambda exc=exc: self._watcher_finished(exc))

    def _watcher_finished(self, exc: Exception | None) -> None:
        self.status_var.set("Stopped")
        if exc is None:
            self.detail_var.set("Watcher stopped")
        else:
            self.detail_var.set(f"Watcher stopped: {exc}")
            messagebox.showerror("Watcher", str(exc))

    def stop_watcher(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.detail_var.set("Stopping watcher...")
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
            if update_detail:
                self.detail_var.set(f"No log file yet: {path}")
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
        raw_lines: deque[str] = deque(maxlen=1000)
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    raw_lines.append(line)
        except OSError as exc:
            if update_detail:
                self.detail_var.set(f"Could not read logs: {exc}")
            return

        rows: list[dict[str, object]] = []
        seen_rows: set[str] = set()
        for line in reversed(raw_lines):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = self._log_row_key(row)
            if key in seen_rows:
                continue
            seen_rows.add(key)
            rows.append(row)
            if len(rows) >= 200:
                break

        for row in rows:
            self.logs_tree.insert(
                "",
                "end",
                values=(
                    row.get("ts", ""),
                    row.get("droid", ""),
                    row.get("rarity", ""),
                    "yes" if row.get("is_priority") else "no",
                    "yes" if row.get("alerted") else "no",
                    f"{float(row.get('score', 0.0)):.2f}" if row.get("score") is not None else "",
                ),
            )
        if update_detail:
            self.detail_var.set(f"Loaded {len(rows)} unique log rows")

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
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("Open", str(exc))

    def toggle_region_overlay(self) -> None:
        if self.region_overlay is not None and self.region_overlay.winfo_exists():
            self.region_overlay.destroy()
            self.region_overlay = None
            return
        set_dpi_awareness()
        capture = create_capture(monitor_index=self.config.monitor_index)
        try:
            screen_w, screen_h = capture.screen_size()
            box, source = RegionResolver(
                screen_w,
                screen_h,
                max_failures=self.config.validation_failures_before_calibration_prompt,
            ).resolve()
            monitor = getattr(capture, "monitor", None)
            left_offset = int(getattr(monitor, "left", 0))
            top_offset = int(getattr(monitor, "top", 0))
            self.show_region_overlay(
                left_offset + box.left,
                top_offset + box.top,
                box.width,
                box.height,
                source,
            )
        finally:
            capture.close()

    def show_region_overlay(self, left: int, top: int, width: int, height: int, source: str) -> None:
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        transparent = "#010203"
        overlay.configure(bg=transparent)
        try:
            overlay.attributes("-transparentcolor", transparent)
            canvas_bg = transparent
        except Exception:
            canvas_bg = "#ff1744"
            overlay.configure(bg=canvas_bg)
        overlay.geometry(f"{width}x{height}+{left}+{top}")
        canvas = tk.Canvas(overlay, width=width, height=height, bg=canvas_bg, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_rectangle(3, 3, width - 4, height - 4, outline="#ff1744", width=5)
        canvas.create_text(
            12,
            12,
            text=f"ToolV2 region: {source}",
            fill="#ff1744",
            anchor="nw",
            font=("Segoe UI", 14, "bold"),
        )
        self.region_overlay = overlay

    def check_updates(self, *, manual: bool) -> None:
        if self.update_check_running:
            return
        config = load_config()
        if not manual and not config.update_check_enabled:
            return
        self.update_check_running = True

        def worker() -> None:
            try:
                release = check_for_update(config)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self._update_check_done(None, exc, manual))
                return
            self.root.after(0, lambda release=release: self._update_check_done(release, None, manual))

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
        if messagebox.askyesno(
            "Update Available",
            f"{release['name']} is available.\n\nOpen the GitHub release page?",
        ):
            webbrowser.open(release["url"])

    def on_close(self) -> None:
        if self._log_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._log_refresh_after_id)
            except Exception:
                pass
        if self.stop_event is not None:
            self.stop_event.set()
        if self.region_overlay is not None and self.region_overlay.winfo_exists():
            self.region_overlay.destroy()
        self.root.destroy()


def run_gui() -> None:
    root = make_root()
    DroidAlertsApp(root)
    root.mainloop()
