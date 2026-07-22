from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .classifier import Detection
from .config import AppConfig, sounds_dir, user_sounds_dir


WAKE_ALARM_FILE = "wake_alarm.wav"


def row_hash(row_bgr: np.ndarray) -> str:
    if row_bgr.size == 0:
        return "empty"
    small = cv2.resize(row_bgr, (96, 24), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.sha1(gray.tobytes()).hexdigest()[:16]


class AlertPolicy:
    """Merged policy: the reference detector's per-combo score gates live inside
    Detection.should_alert; this layer adds target filtering, per-combo
    cooldown, and frame/row-hash dedupe so a persisting chat row doesn't
    re-alert every capture."""

    def __init__(self, config: AppConfig) -> None:
        self._last_combo_alert: dict[tuple[str, str], float] = {}
        self._recent_hashes: dict[str, float] = {}
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        """Adopt new settings without losing cooldown/dedupe state, so the
        watcher can hot-reload config changes mid-run."""
        self.targets = config.targets
        self.cooldown_seconds = config.alert_cooldown_seconds
        self.dedupe_seconds = config.dedupe_seconds
        self.sound_enabled = config.sound_enabled
        self.sound_file = config.sound_file

    def should_alert(self, detection: Detection, row_digest: str) -> bool:
        if (detection.droid, detection.rarity) not in self.targets:
            return False
        if not detection.should_alert:  # reference per-combo threshold table
            return False

        now = time.monotonic()
        seen = self._recent_hashes.get(row_digest)
        self._recent_hashes[row_digest] = now
        if seen is not None and now - seen < self.dedupe_seconds:
            return False

        combo = (detection.droid, detection.rarity)
        last = self._last_combo_alert.get(combo)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        self._last_combo_alert[combo] = now
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - max(self.dedupe_seconds, self.cooldown_seconds) * 4
        self._recent_hashes = {k: v for k, v in self._recent_hashes.items() if v >= cutoff}

    def notify(self, detection: Detection) -> bool:
        if not self.sound_enabled:
            return False

        wav = _alert_wav(self.sound_file)
        if sys.platform == "win32":
            try:
                import winsound

                if wav is not None:
                    winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    winsound.Beep(1200, 220)
                    winsound.Beep(1600, 220)
            except Exception as exc:
                raise RuntimeError(f"Windows could not play the alert sound: {exc}") from exc
            return True

        command: list[str] | None = None
        if sys.platform == "darwin":
            if wav is not None:
                player = shutil.which("afplay")
                if player:
                    command = [player, str(wav)]
            else:
                player = shutil.which("osascript")
                if player:
                    command = [player, "-e", "beep 2"]
        elif wav is not None:
            player = next(
                (path for name in ("paplay", "aplay", "play") if (path := shutil.which(name))),
                None,
            )
            if player:
                command = [player, str(wav)]
        else:
            player = shutil.which("canberra-gtk-play")
            if player:
                command = [player, "-i", "dialog-warning"]
            else:
                player = shutil.which("play")
                if player:
                    command = [
                        player,
                        "-q",
                        "-n",
                        "synth",
                        "0.35",
                        "sine",
                        "1400",
                    ]

        if command is None:
            raise RuntimeError(
                "No supported sound player was found. Install paplay, aplay, SoX, or libcanberra."
            )
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(f"Could not start the alert sound player: {exc}") from exc
        return True


def _alert_wav(preferred: str = "") -> Path | None:
    directories = (user_sounds_dir(), sounds_dir())
    if preferred == "System beeps":
        return None
    if preferred:
        for directory in directories:
            selected = directory / Path(preferred).name
            if (
                selected.name.casefold() != WAKE_ALARM_FILE.casefold()
                and selected.is_file()
                and selected.suffix.lower() == ".wav"
            ):
                return selected
    for directory in directories:
        for path in sorted(directory.glob("*.wav")) if directory.exists() else ():
            if path.name.casefold() != WAKE_ALARM_FILE.casefold():
                return path
    return None


class WakeAlarm:
    """Loop the bundled alarm until explicitly stopped.

    ``start`` returns a generation token. A timed test can pass that token to
    ``stop`` so its three-second callback cannot accidentally stop a newer,
    real alert.
    """

    def __init__(self, wav_path: Path | None = None) -> None:
        self.wav_path = wav_path or (sounds_dir() / WAKE_ALARM_FILE)
        self._lock = threading.Lock()
        self._generation = 0
        self._stop_event: threading.Event | None = None
        self._process: subprocess.Popen | None = None
        self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> int:
        if not self.wav_path.is_file():
            raise RuntimeError(f"Wake alarm sound is missing: {self.wav_path}")
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._active = True

        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(
                    str(self.wav_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
            except Exception as exc:
                with self._lock:
                    self._active = False
                raise RuntimeError(f"Windows could not start the wake alarm: {exc}") from exc
        else:
            try:
                command = self._player_command()
            except Exception:
                with self._lock:
                    self._active = False
                raise
            threading.Thread(
                target=self._loop_player,
                args=(generation, stop_event, command),
                name="DroidAlertsWakeAlarm",
                daemon=True,
            ).start()
        return generation

    def stop(self, generation: int | None = None) -> bool:
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            was_active = self._active
            self._active = False
            stop_event = self._stop_event
            self._stop_event = None
            process = self._process
            self._process = None
        if stop_event is not None:
            stop_event.set()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if sys.platform == "win32" and was_active:
            try:
                import winsound

                winsound.PlaySound(None, 0)
            except Exception:
                pass
        return was_active

    def _player_command(self) -> list[str]:
        if sys.platform == "darwin":
            player = shutil.which("afplay")
            if player:
                return [player, str(self.wav_path)]
        else:
            player = next(
                (path for name in ("paplay", "aplay", "play") if (path := shutil.which(name))),
                None,
            )
            if player:
                return [player, str(self.wav_path)]
        raise RuntimeError("No supported sound player was found for the wake alarm.")

    def _loop_player(
        self,
        generation: int,
        stop_event: threading.Event,
        command: list[str],
    ) -> None:
        while not stop_event.is_set():
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                break
            with self._lock:
                if generation != self._generation or stop_event.is_set():
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    break
                self._process = process
            while process.poll() is None and not stop_event.wait(0.05):
                pass
            if stop_event.is_set() and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        with self._lock:
            if generation == self._generation:
                self._process = None
                self._active = False
