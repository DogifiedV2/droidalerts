from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_THEME_KEY = "signal_dark"


@dataclass(frozen=True)
class AppTheme:
    key: str
    label: str
    ttk_name: str
    theme_type: str
    colors: dict[str, str]
    sidebar_bg: str
    sidebar_fg: str
    sidebar_muted: str
    sidebar_hover: str
    sidebar_active: str
    muted_fg: str
    subtle_bg: str


APP_THEMES: tuple[AppTheme, ...] = (
    AppTheme(
        key="signal_dark",
        label="Default",
        ttk_name="droid_signal",
        theme_type="dark",
        colors={
            "primary": "#39c6d8",
            "secondary": "#667483",
            "success": "#36c98f",
            "info": "#5aa9ff",
            "warning": "#f4b942",
            "danger": "#ef6672",
            "light": "#dce7ef",
            "dark": "#091017",
            "bg": "#0e151d",
            "fg": "#e8f0f6",
            "selectbg": "#184b56",
            "selectfg": "#f7fdff",
            "border": "#2a3743",
            "inputfg": "#edf5fa",
            "inputbg": "#151f29",
            "active": "#1b2935",
        },
        sidebar_bg="#091017",
        sidebar_fg="#f1f7fa",
        sidebar_muted="#91a2af",
        sidebar_hover="#111e28",
        sidebar_active="#16313a",
        muted_fg="#96a5b2",
        subtle_bg="#121c25",
    ),
    AppTheme(
        key="midnight",
        label="Midnight",
        ttk_name="droid_midnight",
        theme_type="dark",
        colors={
            "primary": "#7d8cff",
            "secondary": "#64748b",
            "success": "#4bd3a3",
            "info": "#4db8ff",
            "warning": "#f7c65b",
            "danger": "#f27082",
            "light": "#dfe8ff",
            "dark": "#050a15",
            "bg": "#08101f",
            "fg": "#edf2ff",
            "selectbg": "#28366c",
            "selectfg": "#ffffff",
            "border": "#253453",
            "inputfg": "#edf2ff",
            "inputbg": "#101b31",
            "active": "#172744",
        },
        sidebar_bg="#050a15",
        sidebar_fg="#f1f4ff",
        sidebar_muted="#94a2c3",
        sidebar_hover="#0e1930",
        sidebar_active="#202d5c",
        muted_fg="#98a7c4",
        subtle_bg="#0d1729",
    ),
    AppTheme(
        key="daylight",
        label="Daylight",
        ttk_name="droid_daylight",
        theme_type="light",
        colors={
            "primary": "#137b83",
            "secondary": "#687887",
            "success": "#16865f",
            "info": "#2b6fbd",
            "warning": "#a76500",
            "danger": "#bd3f4f",
            "light": "#f8fafc",
            "dark": "#17232d",
            "bg": "#f3f6f8",
            "fg": "#17232d",
            "selectbg": "#ccebed",
            "selectfg": "#102c31",
            "border": "#d4dde4",
            "inputfg": "#17232d",
            "inputbg": "#ffffff",
            "active": "#e5ecef",
        },
        sidebar_bg="#172630",
        sidebar_fg="#f3f8fa",
        sidebar_muted="#b5c3ca",
        sidebar_hover="#203742",
        sidebar_active="#24515a",
        muted_fg="#62727e",
        subtle_bg="#e9eef1",
    ),
)


_THEMES_BY_KEY = {theme.key: theme for theme in APP_THEMES}
_THEME_ALIASES = {
    alias.casefold().replace("-", "_").replace(" ", "_"): theme.key
    for theme in APP_THEMES
    for alias in (theme.key, theme.label, theme.ttk_name)
}


def normalize_theme_key(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return _THEME_ALIASES.get(normalized, DEFAULT_THEME_KEY)


def theme_for(value: Any) -> AppTheme:
    return _THEMES_BY_KEY[normalize_theme_key(value)]


def theme_label(value: Any) -> str:
    return theme_for(value).label


def theme_labels() -> tuple[str, ...]:
    return tuple(theme.label for theme in APP_THEMES)


def register_app_themes(style: Any) -> None:
    """Register the bundled ttkbootstrap palettes with an existing Style."""

    from ttkbootstrap.style import Colors, ThemeDefinition

    known = set(style.theme_names())
    for theme in APP_THEMES:
        if theme.ttk_name in known:
            continue
        style.register_theme(
            ThemeDefinition(
                name=theme.ttk_name,
                colors=Colors(**theme.colors),
                themetype=theme.theme_type,
            )
        )


def apply_app_theme(
    style: Any,
    value: Any,
    *,
    bootstrap: bool,
    font_family: str = "Segoe UI",
) -> AppTheme:
    """Apply a bundled palette and rebuild the app-specific widget styles."""

    theme = theme_for(value)
    if bootstrap:
        register_app_themes(style)
        style.theme_use(theme.ttk_name)
    else:
        try:
            style.theme_use("clam")
        except Exception:
            pass

    colors = theme.colors
    base_font = (font_family, 10)
    small_font = (font_family, 10)
    title_font = (font_family, 20, "bold")
    section_font = (font_family, 10, "bold")

    style.configure(".", font=base_font, background=colors["bg"], foreground=colors["fg"])
    style.configure("TFrame", background=colors["bg"])
    style.configure("Page.TFrame", background=colors["bg"])
    style.configure("Toolbar.TFrame", background=colors["bg"])
    style.configure(
        "TNotebook",
        background=colors["bg"],
        borderwidth=0,
        bordercolor=colors["bg"],
        lightcolor=colors["bg"],
        darkcolor=colors["bg"],
        tabmargins=0,
    )
    style.configure(
        "Card.TFrame",
        background=colors["bg"],
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        borderwidth=1,
        relief="solid",
    )
    style.configure("Subtle.TFrame", background=theme.subtle_bg)
    style.configure(
        "Subtle.TLabel",
        background=theme.subtle_bg,
        foreground=colors["fg"],
    )
    style.configure(
        "SubtleMuted.TLabel",
        background=theme.subtle_bg,
        foreground=theme.muted_fg,
        font=small_font,
    )
    style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
    style.configure("Muted.TLabel", background=colors["bg"], foreground=theme.muted_fg, font=small_font)
    style.configure("PageTitle.TLabel", background=colors["bg"], foreground=colors["fg"], font=title_font)
    style.configure(
        "SectionTitle.TLabel",
        background=colors["bg"],
        foreground=colors["fg"],
        font=section_font,
    )
    style.configure("Sidebar.TFrame", background=theme.sidebar_bg)
    style.configure("Sidebar.TLabel", background=theme.sidebar_bg, foreground=theme.sidebar_fg)
    style.configure(
        "SidebarNew.TLabel",
        background=colors["primary"],
        foreground=theme.sidebar_bg,
        padding=(6, 2),
        font=(font_family, 8, "bold"),
    )
    style.configure(
        "SidebarMuted.TLabel",
        background=theme.sidebar_bg,
        foreground=theme.sidebar_muted,
        font=small_font,
    )
    style.configure(
        "Sidebar.TButton",
        background=theme.sidebar_bg,
        foreground=theme.sidebar_muted,
        borderwidth=0,
        focusthickness=0,
        focuscolor=theme.sidebar_bg,
        relief="flat",
        anchor="w",
        padding=(18, 14),
        font=(font_family, 11),
    )
    style.map(
        "Sidebar.TButton",
        background=[("pressed", theme.sidebar_active), ("active", theme.sidebar_hover)],
        foreground=[("pressed", theme.sidebar_fg), ("active", theme.sidebar_fg)],
    )
    style.configure(
        "SidebarLink.TButton",
        background=theme.sidebar_active,
        foreground=colors["primary"],
        bordercolor=theme.sidebar_hover,
        lightcolor=theme.sidebar_hover,
        darkcolor=theme.sidebar_hover,
        borderwidth=1,
        focusthickness=0,
        focuscolor=colors["primary"],
        relief="flat",
        anchor="w",
        padding=(18, 14),
        font=(font_family, 11, "bold"),
    )
    style.map(
        "SidebarLink.TButton",
        background=[("pressed", colors["primary"]), ("active", theme.sidebar_hover)],
        foreground=[("pressed", theme.sidebar_bg), ("active", theme.sidebar_fg)],
    )
    style.configure(
        "SidebarActive.TButton",
        background=theme.sidebar_active,
        foreground=theme.sidebar_fg,
        borderwidth=0,
        focusthickness=0,
        focuscolor=theme.sidebar_active,
        relief="flat",
        anchor="w",
        padding=(18, 14),
        font=(font_family, 11, "bold"),
    )
    style.map(
        "SidebarActive.TButton",
        background=[("pressed", theme.sidebar_active), ("active", theme.sidebar_active)],
        foreground=[("disabled", theme.sidebar_fg), ("active", theme.sidebar_fg)],
    )
    style.configure(
        "SidebarStatus.TLabel",
        background=theme.sidebar_bg,
        foreground=theme.sidebar_fg,
        padding=(0, 4),
        font=(font_family, 10, "bold"),
    )
    style.configure(
        "Link.TButton",
        background=colors["bg"],
        foreground=colors["primary"],
        borderwidth=0,
        focusthickness=0,
        relief="flat",
        padding=(3, 2),
        font=small_font,
    )
    style.map(
        "Link.TButton",
        background=[("active", colors["bg"]), ("pressed", colors["bg"])],
        foreground=[("active", colors["info"]), ("pressed", colors["primary"])],
    )
    style.configure(
        "Utility.TButton",
        background=colors["active"],
        foreground=colors["fg"],
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        focuscolor=colors["primary"],
        borderwidth=1,
        relief="flat",
        padding=(12, 8),
    )
    style.map(
        "Utility.TButton",
        background=[("pressed", colors["selectbg"]), ("active", colors["selectbg"])],
        foreground=[("pressed", colors["selectfg"]), ("active", colors["selectfg"])],
    )
    advanced_style = "Advanced.Round.Toggle" if bootstrap else "Advanced.TCheckbutton"
    if bootstrap:
        try:
            style.layout(advanced_style, style.layout("Round.Toggle"))
        except Exception:
            advanced_style = "Advanced.TCheckbutton"
    style.configure(
        advanced_style,
        background=colors["bg"],
        foreground=colors["fg"],
        font=(font_family, 11, "bold"),
        padding=(4, 6),
    )
    style.map(
        advanced_style,
        background=[("selected", colors["bg"]), ("active", colors["bg"])],
        foreground=[("disabled", theme.muted_fg), ("active", colors["fg"])],
    )
    style.configure("TButton", padding=(12, 8))
    style.configure("TCheckbutton", padding=(2, 4))
    style.configure("TEntry", padding=(8, 6))
    style.configure("TCombobox", padding=(7, 5))
    style.configure("TSpinbox", padding=(7, 5))
    style.configure(
        "Treeview",
        rowheight=30,
        borderwidth=0,
        background=colors["inputbg"],
        fieldbackground=colors["inputbg"],
        foreground=colors["inputfg"],
    )
    style.configure(
        "Treeview.Heading",
        padding=(8, 8),
        font=section_font,
        background=theme.subtle_bg,
        foreground=colors["fg"],
    )
    style.map(
        "Treeview",
        background=[("selected", colors["selectbg"])],
        foreground=[("selected", colors["selectfg"])],
    )
    return theme
