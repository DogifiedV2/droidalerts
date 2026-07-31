pragma Singleton
import QtQuick 2.15

QtObject {
    readonly property color bg0: "#080d13"
    readonly property color bg1: "#0e151d"
    readonly property color bg2: "#121b25"
    readonly property color bg3: "#182330"
    readonly property color bgHover: "#1d2a38"
    readonly property color surfaceRaised: "#15202b"
    readonly property color line: "#243140"
    readonly property color lineSoft: "#1a2530"
    readonly property color lineRaised: "#2e4051"
    readonly property color ink: "#e9f1f7"
    readonly property color muted: "#8ba0ae"
    readonly property color dim: "#5b6f7d"
    readonly property color accent: "#39c6d8"
    readonly property color accentSoft: "#18333c"
    readonly property color good: "#36c98f"
    readonly property color warning: "#f4b942"
    readonly property color danger: "#ef6672"
    readonly property color galactic: "#b44df0"
    readonly property color mythic: "#ff4fae"
    readonly property color diamond: "#61d6ff"
    readonly property color gold: "#ffd34d"
    readonly property color beskar: "#cdd2de"
    readonly property var rainbowColors: [
        "#ff5252",
        "#ff9f2e",
        "#ffe14d",
        "#5ce06b",
        "#42c9ff",
        "#b06bff",
        "#ff6bd6"
    ]

    readonly property string bodyFont: Qt.platform.os === "windows" ? "Segoe UI" :
                                        Qt.platform.os === "osx" ? "Helvetica Neue" : "Noto Sans"
    readonly property string displayFont: Qt.platform.os === "windows" ? "Bahnschrift" :
                                           Qt.platform.os === "osx" ? "Avenir Next" : "Noto Sans"
    readonly property string monoFont: Qt.platform.os === "windows" ? "Cascadia Code" :
                                        Qt.platform.os === "osx" ? "Menlo" : "Noto Sans Mono"

    function toneColor(tone) {
        if (tone === "good") return good
        if (tone === "warning") return warning
        if (tone === "danger") return danger
        if (tone === "accent" || tone === "info") return accent
        if (tone === "galactic") return galactic
        if (tone === "mythic") return mythic
        if (tone === "diamond") return diamond
        if (tone === "gold") return gold
        if (tone === "beskar") return beskar
        if (tone === "rainbow") return rainbowColors[6]
        return muted
    }
}
