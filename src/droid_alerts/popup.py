from __future__ import annotations

import ctypes
import sys
import threading
from dataclasses import dataclass
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
    "Rebirth": "#ffb11b",
}
RAINBOW_LETTERS = ["#ff5252", "#ff9f2e", "#ffe14d", "#5ce06b", "#42c9ff", "#b06bff", "#ff6bd6"]
DROID_ACCENTS = {
    "Default": ("#9ca3af", "#3f4652"),
    "Gold": ("#ffd34d", "#725517"),
    "Diamond": ("#3fd9ff", "#155a6e"),
    "Rainbow": ("#c05cff", "#4d2470"),
    "Beskar": ("#c9cdd9", "#4d5160"),
    "Galactic": ("#7d8cff", "#30396e"),
    "Rebirth": ("#ffb11b", "#6b4210"),
}
RARITY_COLORS = {
    "Default": "#e8e8e8",
    "Gold": "#ffd34d",
    "Diamond": "#3fd9ff",
    "Beskar": "#e8eaf0",
    "Rainbow": "#ff65d8",
    "Belt": "#65f3ff",
    "Common": "#e8e8e8",
    "Rare": "#3fd9ff",
    "Epic": "#a95cff",
    "Legendary": "#ff9d2e",
    "Mythic": "#ff3fa8",
}
CARD_BG = "#12141f"
CARD_BG_SOFT = "#1a1d2c"
ICON_HAND_X = 34
ICON_HAND_Y = 50
ICON_HAND_DROP = 10


@dataclass(frozen=True)
class _PopupLayout:
    width: int
    height: int
    panel_width: int
    panel_height: int
    margin: int
    ui_scale: float
    show_icon: bool


def _calculate_popup_layout(
    screen_width: int,
    screen_height: int,
    scale: float,
    *,
    icon_width: int = 0,
    icon_height: int = 0,
) -> _PopupLayout:
    """Fit the card and optional character inside the selected monitor."""

    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    ui_scale = min(1.5, max(0.7, float(scale)))
    margin = max(16, int(24 * ui_scale))
    available_width = max(1, screen_width - margin * 2)
    available_height = max(1, screen_height - margin * 2)
    desired_panel_height = int(
        min(185, max(150, screen_height - 160)) * ui_scale
    )

    icon_overhang = max(0, int(icon_width) - ICON_HAND_X + 22)
    icon_height_extra = max(
        0,
        int(icon_height) - ICON_HAND_Y + ICON_HAND_DROP,
    ) + 10
    minimum_panel_height = max(88, int(105 * ui_scale))
    show_icon = bool(
        icon_width > 0
        and icon_height > 0
        and available_width - icon_overhang >= 220
        and available_height - icon_height_extra >= minimum_panel_height
    )

    if show_icon:
        desired_panel_width = int(
            min(
                880,
                max(620, screen_width - int(icon_width) - 90 + ICON_HAND_X),
            )
            * ui_scale
        )
        panel_width = min(desired_panel_width, available_width - icon_overhang)
        panel_height = min(desired_panel_height, available_height - icon_height_extra)
        return _PopupLayout(
            width=panel_width + icon_overhang,
            height=panel_height + icon_height_extra,
            panel_width=panel_width,
            panel_height=panel_height,
            margin=margin,
            ui_scale=ui_scale,
            show_icon=True,
        )

    desired_width = int(min(1050, max(420, screen_width - 80)) * ui_scale)
    width = min(desired_width, available_width)
    panel_height = min(desired_panel_height, available_height)
    return _PopupLayout(
        width=width,
        height=panel_height,
        panel_width=width,
        panel_height=panel_height,
        margin=margin,
        ui_scale=ui_scale,
        show_icon=False,
    )


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


def _is_belt_detection(detection: Detection) -> bool:
    return detection.source == "belt-tracker" or detection.rarity == "Belt"


def _uses_attribute_rarity(detection: Detection) -> bool:
    return _is_belt_detection(detection) or detection.source == "limited-deal"


def _is_rebirth_detection(detection: Detection) -> bool:
    return detection.source == "rebirth-alert"


def _title_segments(detection: Detection) -> list[tuple[str, str]]:
    if _is_rebirth_detection(detection):
        return [("REBIRTH DROID AVAILABLE", DROID_TEXT_COLORS["Rebirth"])]
    attributes = (
        detection.rarity.split()
        if _uses_attribute_rarity(detection) and detection.rarity != "Belt"
        else [detection.rarity]
    )
    segments = [
        (attribute.upper() + " ", RARITY_COLORS.get(attribute, "#ffffff"))
        for attribute in attributes
    ]
    name = detection.droid.upper()
    if detection.droid == "Rainbow":
        segments.extend(
            (letter, RAINBOW_LETTERS[i % len(RAINBOW_LETTERS)]) for i, letter in enumerate(name)
        )
    else:
        segments.append((name, DROID_TEXT_COLORS.get(detection.droid, "#ffffff")))
    return segments


def _title_lines(detection: Detection) -> list[list[tuple[str, str]]]:
    """Split belt/deal attributes from the droid name to keep alerts legible."""

    segments = _title_segments(detection)
    if _is_rebirth_detection(detection):
        return [segments]
    if not _uses_attribute_rarity(detection) or detection.rarity == "Belt":
        return [segments]
    attribute_count = len(detection.rarity.split())
    attributes = list(segments[:attribute_count])
    if attributes:
        text, color = attributes[-1]
        attributes[-1] = (text.rstrip(), color)
    name = list(segments[attribute_count:])
    return [line for line in (attributes, name) if line]


def _fitted_font(
    window: "tk.Misc",
    segments: list[tuple[str, str]],
    *,
    family: str,
    preferred_size: int,
    minimum_size: int,
    max_width: int,
    weight: str | None = None,
) -> "tkfont.Font":
    """Create the largest font that keeps every segment inside max_width."""

    preferred_size = max(1, int(preferred_size))
    minimum_size = max(1, min(preferred_size, int(minimum_size)))
    font_options: dict[str, object] = {
        "root": window,
        "family": family,
        "size": preferred_size,
    }
    if weight is not None:
        font_options["weight"] = weight
    font_obj = tkfont.Font(**font_options)

    def measured_width() -> int:
        return sum(font_obj.measure(text) for text, _color in segments)

    width = measured_width()
    if width <= max_width or width <= 0:
        return font_obj
    size = max(minimum_size, int(preferred_size * max_width / width))
    font_obj.configure(size=size)
    while size > minimum_size and measured_width() > max_width:
        size -= 1
        font_obj.configure(size=size)
    return font_obj


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
        icon = None
        if icon_path and icon_path.exists():
            try:
                # master= binds the image to THIS window's interpreter; the
                # process default root may belong to another overlay's Tk
                # (e.g. Droid Timers), which breaks with "pyimageN doesn't
                # exist".
                icon = tk.PhotoImage(master=window, file=str(icon_path))
            except Exception as exc:
                print(f"[POPUP] Failed to load icon: {exc}")
                icon = None

        layout = _calculate_popup_layout(
            screen_w,
            screen_h,
            scale,
            icon_width=icon.width() if icon is not None else 0,
            icon_height=icon.height() if icon is not None else 0,
        )
        if not layout.show_icon:
            icon = None
        width = layout.width
        height = layout.height
        panel_width = layout.panel_width
        panel_height = layout.panel_height
        margin = layout.margin
        ui_scale = layout.ui_scale
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

        accent_key = (
            "Rebirth"
            if _is_rebirth_detection(detection)
            else (
                detection.rarity.split(" ", 1)[0]
                if _uses_attribute_rarity(detection)
                else detection.droid
            )
        )
        accent, accent_dim = DROID_ACCENTS.get(accent_key, ("#ff0055", "#5a0f2c"))

        # Card: soft outer ring + accent border + dark rounded body.
        _rounded_rect(canvas, 0, 0, panel_width - 1, panel_height - 1, 20, fill=accent_dim, outline="")
        _rounded_rect(canvas, 3, 3, panel_width - 4, panel_height - 4, 18, fill=accent, outline="")
        _rounded_rect(canvas, 6, 6, panel_width - 7, panel_height - 7, 16, fill=CARD_BG, outline="")
        # Subtle inner header band.
        header_height = min(
            max(40, int(56 * ui_scale)),
            max(40, panel_height - 44),
        )
        _rounded_rect(
            canvas,
            6,
            6,
            panel_width - 7,
            header_height,
            16,
            fill=CARD_BG_SOFT,
            outline="",
        )
        canvas.create_rectangle(
            6,
            max(6, header_height - 16),
            panel_width - 7,
            header_height,
            fill=CARD_BG_SOFT,
            outline="",
        )

        center_x = panel_width // 2
        caption_font = tkfont.Font(
            root=window, family="Segoe UI", size=max(9, int(13 * ui_scale)), weight="bold"
        )

        caption = (
            "REBIRTH ALERT"
            if _is_rebirth_detection(detection)
            else (
                "LIMITED DEAL"
                if detection.source == "limited-deal"
                else (
                    "BELT ALERT"
                    if _is_belt_detection(detection)
                    else ("PRIORITY SPAWN" if detection.is_priority else "DROID SPAWN")
                )
            )
        )
        canvas.create_text(
            center_x,
            header_height // 2 + 3,
            text=" ".join(caption),
            fill=accent,
            font=caption_font,
            anchor="center",
        )

        title_lines = _title_lines(detection)
        title_width = max(40, panel_width - max(28, int(40 * ui_scale)))
        body_top = header_height + max(4, int(6 * ui_scale))
        body_bottom = panel_height - max(6, int(10 * ui_scale))
        body_center = (body_top + body_bottom) // 2
        if len(title_lines) == 1:
            title_font = _fitted_font(
                window,
                title_lines[0],
                family="Segoe UI Black",
                preferred_size=max(20, int(32 * ui_scale)),
                minimum_size=max(11, int(14 * ui_scale)),
                max_width=title_width,
            )
            _draw_segments(canvas, title_lines[0], center_x, body_center, title_font)
        else:
            attribute_font = _fitted_font(
                window,
                title_lines[0],
                family="Segoe UI",
                preferred_size=max(12, int(18 * ui_scale)),
                minimum_size=max(9, int(11 * ui_scale)),
                max_width=title_width,
                weight="bold",
            )
            name_font = _fitted_font(
                window,
                title_lines[1],
                family="Segoe UI Black",
                preferred_size=max(19, int(31 * ui_scale)),
                minimum_size=max(11, int(14 * ui_scale)),
                max_width=title_width,
            )
            attribute_height = int(attribute_font.metrics("linespace"))
            name_height = int(name_font.metrics("linespace"))
            line_gap = max(2, int(4 * ui_scale))
            total_height = attribute_height + line_gap + name_height
            attribute_y = body_center - total_height // 2 + attribute_height // 2
            name_y = attribute_y + attribute_height // 2 + line_gap + name_height // 2
            _draw_segments(canvas, title_lines[0], center_x, attribute_y, attribute_font)
            _draw_segments(canvas, title_lines[1], center_x, name_y, name_font)

        if icon is not None:
            window._droid_alerts_popup_icon = icon  # type: ignore[attr-defined]
            icon_left = panel_width - ICON_HAND_X + 3
            icon_top = panel_height - ICON_HAND_Y + ICON_HAND_DROP
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
