from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path

from .classifier import Detection
from .capture import MonitorInfo, format_tk_geometry
from .config import AppConfig, assets_dir

try:
    import tkinter as tk
    from tkinter import font as tkfont
except Exception:  # pragma: no cover - tkinter availability is platform dependent.
    tk = None
    tkfont = None


# Visual theme: text colored by droid family (Rainbow gets per-letter colors),
# rarity shown as a colored pill, card border glows in the droid accent.
DROID_TEXT_COLORS = {
    "Diamond": "#3fd9ff",
    "Beskar": "#e8eaf0",
}
RAINBOW_LETTERS = ["#ff5252", "#ff9f2e", "#ffe14d", "#5ce06b", "#42c9ff", "#b06bff", "#ff6bd6"]
DROID_ACCENTS = {
    "Diamond": ("#3fd9ff", "#155a6e"),
    "Rainbow": ("#c05cff", "#4d2470"),
    "Beskar": ("#c9cdd9", "#4d5160"),
}
RARITY_COLORS = {
    "Common": "#e8e8e8",
    "Rare": "#3fd9ff",
    "Epic": "#a95cff",
    "Legendary": "#ff9d2e",
    "Mythic": "#ff3fa8",
}
CARD_BG = "#12141f"
CARD_BG_SOFT = "#1a1d2c"


def _apply_win32_color_key(window: "tk.Misc", color_hex: str) -> bool:
    """Force the transparency color key at the WinAPI level.

    Tk's -transparentcolor can silently fail on some window paths (e.g.
    Toplevels under themed parents), leaving an opaque gray rectangle around
    the card and character. A layered-window color key works regardless.
    """
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        ws_ex_noactivate = 0x08000000
        lwa_colorkey = 0x00000001
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)
        colorref = r | (g << 8) | (b << 16)
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        user32.SetWindowLongW(
            hwnd, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent | ws_ex_noactivate
        )
        return bool(user32.SetLayeredWindowAttributes(hwnd, colorref, 0, lwa_colorkey))
    except Exception:
        return False


def _rounded_rect(canvas: "tk.Canvas", x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> int:
    r = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _title_segments(detection: Detection) -> list[tuple[str, str]]:
    rarity_color = RARITY_COLORS.get(detection.rarity, "#ffffff")
    segments = [(detection.rarity.upper() + " ", rarity_color)]
    name = detection.droid.upper()
    if detection.droid == "Rainbow":
        segments.extend(
            (letter, RAINBOW_LETTERS[i % len(RAINBOW_LETTERS)]) for i, letter in enumerate(name)
        )
    else:
        segments.append((name, DROID_TEXT_COLORS.get(detection.droid, "#ffffff")))
    return segments


def _draw_segments(
    canvas: "tk.Canvas",
    segments: list[tuple[str, str]],
    center_x: int,
    y: int,
    font_obj: "tkfont.Font",
) -> None:
    total = sum(font_obj.measure(text) for text, _color in segments)
    x = center_x - total // 2
    for text, color in segments:
        # Cheap outline for readability over the transparent desktop edge.
        canvas.create_text(x + 2, y + 2, text=text, fill="#05060a", font=font_obj, anchor="w")
        canvas.create_text(x, y, text=text, fill=color, font=font_obj, anchor="w")
        x += font_obj.measure(text)


def popup_icon_path(config: AppConfig) -> Path:
    return assets_dir() / config.popup_icon_file


def bring_popup_to_front(root: "tk.Tk", x: int, y: int, width: int, height: int) -> None:
    root.lift()
    root.attributes("-topmost", True)
    if sys.platform != "win32":
        return
    try:
        hwnd = root.winfo_id()
        hwnd_topmost = -1
        swp_showwindow = 0x0040
        swp_noactivate = 0x0010
        ctypes.windll.user32.SetWindowPos(
            hwnd, hwnd_topmost, x, y, width, height, swp_showwindow | swp_noactivate
        )
    except Exception:
        pass


def show_popup(
    detection: Detection,
    popup_seconds: float,
    *,
    icon_path: Path | None = None,
    parent: "tk.Misc | None" = None,
    monitor: MonitorInfo | None = None,
    position: str = "top_center",
    scale: float = 1.0,
    opacity: float = 1.0,
) -> None:
    if tk is None:
        return

    def create_popup(window, *, owns_mainloop: bool) -> None:
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        try:
            window.attributes("-alpha", min(1.0, max(0.55, float(opacity))))
        except Exception:
            pass
        transparent = "#010203"
        window.configure(bg=transparent)
        tk_transparency = False
        try:
            window.attributes("-transparentcolor", transparent)
            tk_transparency = True
        except Exception:
            pass
        if tk_transparency or sys.platform == "win32":
            # Win32 color key applied after mapping (below) backs up Tk's
            # attribute, which can silently fail on some Toplevel paths.
            canvas_bg = transparent
        else:
            window.configure(bg=CARD_BG)
            canvas_bg = CARD_BG

        screen_w = monitor.width if monitor is not None else window.winfo_screenwidth()
        screen_h = monitor.height if monitor is not None else window.winfo_screenheight()
        screen_left = monitor.left if monitor is not None else 0
        screen_top = monitor.top if monitor is not None else 0
        ui_scale = min(1.5, max(0.7, float(scale)))
        # Native 128px icon looks crisp; 2x zoom was blurry. 1.5x via
        # zoom(3)/subsample(2) if a bigger character is ever wanted again.
        panel_height = int(min(185, max(150, screen_h - 160)) * ui_scale)
        icon = None
        has_icon = bool(icon_path and icon_path.exists())
        if has_icon:
            try:
                # master= binds the image to THIS window's interpreter; the
                # process default root may belong to another overlay's Tk
                # (e.g. Droid Timers), which breaks with "pyimageN doesn't
                # exist".
                icon = tk.PhotoImage(master=window, file=str(icon_path))
            except Exception as exc:
                print(f"[POPUP] Failed to load icon: {exc}")
                icon = None
                has_icon = False

        if icon is not None:
            # Original image hand point. The panel's bottom-right corner
            # lands just above here so the box sits on the raised hand.
            hand_x = 34
            hand_y = 50
            hand_drop = 10
            panel_width = int(min(880, max(620, screen_w - icon.width() - 90 + hand_x)) * ui_scale)
            width = panel_width + icon.width() - hand_x + 22
            height = panel_height + max(0, icon.height() - hand_y + hand_drop) + 10
        else:
            width = int(min(1050, max(420, screen_w - 80)) * ui_scale)
            panel_width = width
            height = panel_height

        margin = max(16, int(24 * ui_scale))
        width = min(width, max(220, screen_w - margin * 2))
        height = min(height, max(120, screen_h - margin * 2))
        panel_width = min(panel_width, width)
        if position.endswith("left"):
            x = screen_left + margin
        elif position.endswith("right"):
            x = screen_left + max(margin, screen_w - width - margin)
        else:
            x = screen_left + max(0, (screen_w - width) // 2)
        if position.startswith("bottom"):
            y = screen_top + max(margin, screen_h - height - margin)
        else:
            # Keep top alerts below the default timer-strip position.
            available_y = max(margin, screen_h - height - margin)
            y = screen_top + min(max(92, margin), available_y)
        window.geometry(format_tk_geometry(width=width, height=height, x=x, y=y))

        canvas = tk.Canvas(window, width=width, height=height, bg=canvas_bg, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        # Themed parents (ttkbootstrap GUI) override the constructor bg with
        # the theme color, which breaks the transparency color key; an
        # explicit post-creation configure always wins.
        canvas.configure(bg=canvas_bg, highlightthickness=0)

        accent, accent_dim = DROID_ACCENTS.get(detection.droid, ("#ff0055", "#5a0f2c"))

        # Card: soft outer ring + accent border + dark rounded body.
        _rounded_rect(canvas, 0, 0, panel_width - 1, panel_height - 1, 20, fill=accent_dim, outline="")
        _rounded_rect(canvas, 3, 3, panel_width - 4, panel_height - 4, 18, fill=accent, outline="")
        _rounded_rect(canvas, 6, 6, panel_width - 7, panel_height - 7, 16, fill=CARD_BG, outline="")
        # Subtle inner header band.
        _rounded_rect(canvas, 6, 6, panel_width - 7, 56, 16, fill=CARD_BG_SOFT, outline="")
        canvas.create_rectangle(6, 40, panel_width - 7, 56, fill=CARD_BG_SOFT, outline="")

        center_x = panel_width // 2
        caption_font = tkfont.Font(
            root=window, family="Segoe UI", size=max(9, int(13 * ui_scale)), weight="bold"
        )
        title_font = tkfont.Font(
            root=window, family="Segoe UI Black", size=max(20, int(32 * ui_scale))
        )

        caption = (
            "BELT ALERT"
            if detection.rarity == "Belt"
            else ("PRIORITY SPAWN" if detection.is_priority else "DROID SPAWN")
        )
        canvas.create_text(
            center_x, 32, text=" ".join(caption), fill=accent, font=caption_font, anchor="center"
        )

        # Single title line, e.g. "MYTHIC BESKAR", centered in the body
        # below the header band.
        title_y = (56 + panel_height - 8) // 2
        _draw_segments(canvas, _title_segments(detection), center_x, title_y, title_font)

        if icon is not None:
            window._droid_alerts_popup_icon = icon  # type: ignore[attr-defined]
            icon_left = panel_width - hand_x + 3
            icon_top = panel_height - hand_y + hand_drop
            canvas.create_image(icon_left, icon_top, image=icon, anchor="nw")

        window.update_idletasks()
        if canvas_bg == transparent:
            _apply_win32_color_key(window, transparent)
        bring_popup_to_front(window, x, y, width, height)
        window.after(int(max(0.5, popup_seconds) * 1000), window.destroy)
        if owns_mainloop:
            window.mainloop()

    def popup_thread() -> None:
        try:
            root = tk.Tk()
            create_popup(root, owns_mainloop=True)
        except Exception as exc:
            print(f"[POPUP] Failed to show alert: {exc}")

    if parent is not None:
        def parent_popup() -> None:
            try:
                create_popup(tk.Toplevel(parent), owns_mainloop=False)
            except Exception as exc:
                print(f"[POPUP] Failed to show GUI popup: {exc}")

        try:
            parent.after(0, parent_popup)
            return
        except Exception as exc:
            print(f"[POPUP] Failed to schedule GUI popup, using standalone popup: {exc}")

    threading.Thread(target=popup_thread, daemon=True).start()
