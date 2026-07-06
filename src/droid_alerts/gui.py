from __future__ import annotations

import json
import os
import threading
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import BooleanVar, DoubleVar, IntVar, StringVar, messagebox

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
from .config import AppConfig, config_dir, load_config, project_root, save_config
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
        self.root.title("Droid Alerts")
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
        self._autosave_after_id: str | None = None
        self._loading_settings = False
        self._autosave_ready = False

        self.status_var = StringVar(value="Stopped")
        self.detail_var = StringVar(value=f"Config: {config_dir() / 'config.json'}")
        self.setting_vars: dict[str, object] = {}
        self.alert_vars: dict[tuple[str, str], BooleanVar] = {}
        self.advanced_widgets: list[object] = []

        self._build_ui()
        self.load_settings()
        self._wire_auto_save()
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

        ttk.Label(header, text="Droid Alerts", font=("Segoe UI", 20, "bold")).grid(
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

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.settings_tab = ttk.Frame(self.notebook, padding=12)
        self.runtime_tab = ttk.Frame(self.notebook, padding=12)
        self.logs_tab = ttk.Frame(self.notebook, padding=12)
        self.files_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.runtime_tab, text="Runtime")
        self.notebook.add(self.logs_tab, text="Logs")
        self.notebook.add(self.files_tab, text="Files")

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
        ttk.Button(actions, text="Reload", command=self.load_settings).grid(row=0, column=1)

    def _add_settings_actions(self, parent, *, row: int, columnspan: int = 1) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=columnspan, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            actions,
            text="Advanced settings",
            variable=self.setting_vars["advanced_mode"],
            command=self.on_advanced_toggle,
            **bootstyle("round-toggle"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Reload", command=self.load_settings).grid(row=0, column=2)

    def _build_settings_tab(self) -> None:
        self.settings_tab.columnconfigure(0, weight=0, minsize=285)
        self.settings_tab.columnconfigure(1, weight=1, minsize=560)
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

        options_outer, options_frame = self._labeled_section(self.settings_tab, "Options")
        options_outer.grid(row=0, column=1, sticky="nsew")
        options_frame.columnconfigure(0, weight=1, minsize=380)
        options_frame.columnconfigure(1, minsize=150)
        simple_rows = (
            ("popup_enabled", "Popup alerts", None),
            ("sound_enabled", "Sound alerts", None),
            ("phone_alerts_enabled", "Pushover phone alerts", self.on_phone_alert_toggle),
            ("ntfy_enabled", "ntfy phone alerts", self.on_ntfy_alert_toggle),
            ("discord_enabled", "Discord webhook alerts", self.on_discord_alert_toggle),
            ("extra_checks", "Extra checks (fixes washed-out colors, e.g. HDR)", None),
        )
        advanced_rows = (
            ("save_alert_samples", "Save alert samples", None),
            ("ntfy_include_attachment", "Attach alert sample to ntfy", None),
            ("phone_include_attachment", "Attach alert sample to Pushover", None),
            ("update_check_enabled", "Check GitHub releases on startup", None),
        )
        for key, _label, _command in simple_rows + advanced_rows:
            self.setting_vars[key] = BooleanVar(value=False)
        for row, (key, label, command) in enumerate(simple_rows + advanced_rows):
            check = ttk.Checkbutton(
                options_frame,
                text=label,
                variable=self.setting_vars[key],
                command=command,
            )
            check.grid(row=row, column=0, sticky="w", pady=3)
            if (key, label, command) in advanced_rows:
                self.advanced_widgets.append(check)

        setup_buttons = (
            (2, "Set Up Pushover", self.setup_phone_alerts_and_enable, "warning-outline"),
            (3, "Set Up ntfy", self.setup_ntfy_alerts_and_enable, "success-outline"),
            (4, "Set Up Discord", self.setup_discord_alerts_and_enable, "info-outline"),
        )
        for row, label, command, style in setup_buttons:
            ttk.Button(
                options_frame, text=label, command=command, **bootstyle(style)
            ).grid(row=row, column=1, padx=(12, 0), sticky="ew")

        self.setting_vars["advanced_mode"] = BooleanVar(value=False)
        self._add_settings_actions(self.settings_tab, row=1, columnspan=2)

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
        self.setting_vars["save_debug_screenshots"] = BooleanVar(value=False)

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

        debug_row = (len(fields) + 1) // 2
        ttk.Checkbutton(
            runtime_frame,
            text="Debug mode",
            variable=self.setting_vars["save_debug_screenshots"],
        ).grid(row=debug_row, column=0, columnspan=2, sticky="w", pady=(8, 2))

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

    def on_advanced_toggle(self) -> None:
        advanced = bool(self._value("advanced_mode"))
        self._apply_advanced_visibility(advanced)
        self._schedule_auto_save()

    def _apply_advanced_visibility(self, advanced: bool) -> None:
        for widget in self.advanced_widgets:
            if advanced:
                widget.grid()
            else:
                widget.grid_remove()
        try:
            if advanced:
                self.notebook.add(self.runtime_tab)
            else:
                self.notebook.hide(self.runtime_tab)
        except Exception:
            pass

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
                "save_alert_samples",
                "ntfy_enabled",
                "discord_enabled",
                "phone_alerts_enabled",
                "ntfy_include_attachment",
                "phone_include_attachment",
                "update_check_enabled",
                "extra_checks",
                "save_debug_screenshots",
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
            self._set_var("advanced_mode", self.config.advanced_mode)
            self._apply_advanced_visibility(self.config.advanced_mode)
            self.detail_var.set(f"Settings loaded from {config_dir() / 'config.json'}")
        finally:
            self._loading_settings = False

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
        self.save_settings(interactive=False, update_detail=False)

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
        except (TypeError, ValueError) as exc:
            if interactive:
                messagebox.showerror("Settings", f"Invalid numeric setting: {exc}")
            elif update_detail:
                self.detail_var.set("Settings not saved: invalid numeric value")
            return None

        config.sound_enabled = bool(self._value("sound_enabled"))
        config.popup_enabled = bool(self._value("popup_enabled"))
        config.save_alert_samples = bool(self._value("save_alert_samples"))
        config.save_debug_screenshots = bool(self._value("save_debug_screenshots"))
        config.ntfy_enabled = bool(self._value("ntfy_enabled"))
        config.discord_enabled = bool(self._value("discord_enabled"))
        config.phone_alerts_enabled = bool(self._value("phone_alerts_enabled"))
        config.ntfy_include_attachment = bool(self._value("ntfy_include_attachment"))
        config.phone_include_attachment = bool(self._value("phone_include_attachment"))
        config.update_check_enabled = bool(self._value("update_check_enabled"))
        config.extra_checks = bool(self._value("extra_checks"))
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
    ) -> dict[str, str] | None:
        """Styled modal dialog for setup flows.

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

        ttk.Label(body, text=title, font=("Segoe UI", 14, "bold")).grid(
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
                step_frame, text=f"{index}.", font=("Segoe UI", 10, "bold"), **bootstyle("info")
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
            ttk.Label(body, text=label, font=("Segoe UI", 10, "bold")).grid(
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
                font=("Segoe UI", 10),
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

        ttk.Button(
            buttons, text=cancel_text, command=lambda: finish(None), **bootstyle("secondary")
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text=ok_text, command=accept, **bootstyle("success")).grid(
            row=0, column=1
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
            return
        config = load_config()
        config.ntfy_server_url = str(self._value("ntfy_server_url")).strip() or "https://ntfy.sh"
        config.ntfy_topic = str(self._value("ntfy_topic")).strip()
        if not ntfy_configured(config) and not self.setup_ntfy_alerts_and_enable():
            self.detail_var.set("ntfy alerts stay off until a topic is set up")

    def on_discord_alert_toggle(self) -> None:
        if not bool(self._value("discord_enabled")):
            return
        if not discord_webhook_configured(load_config()) and not self.setup_discord_alerts_and_enable():
            self.detail_var.set("Discord alerts stay off until a webhook is set up")

    def on_phone_alert_toggle(self) -> None:
        if not bool(self._value("phone_alerts_enabled")):
            return
        if not phone_alerts_configured(load_config()) and not self.setup_phone_alerts_and_enable():
            self.detail_var.set("Pushover alerts stay off until the keys are set up")

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
            self.detail_var.set(f"Test alert sent: {', '.join(sent_channels)}. {event_text(detection)}")
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
        mode = "debug on" if config.save_debug_screenshots else "debug off"
        self.detail_var.set(f"Watcher started ({mode})")

    def _watch_thread(self, config: AppConfig, stop_event: threading.Event) -> None:
        try:
            run_watch(
                debug=config.save_debug_screenshots,
                config=config,
                stop_event=stop_event,
                popup_parent=self.root,
            )
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
            text=f"Droid Alerts region: {source}",
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
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None
            self.save_settings(interactive=False, update_detail=False)
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
