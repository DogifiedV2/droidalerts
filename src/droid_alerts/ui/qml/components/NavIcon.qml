import QtQuick 2.15
import QtQuick.Shapes 1.15
import DroidAlerts.Components 1.0

Item {
    id: root
    property string name: ""
    property color tint: Theme.muted
    property real weight: 1.75

    implicitWidth: 18
    implicitHeight: 18

    readonly property var paths: ({
        "dashboard": [
            "M3.5 3.5h7v7h-7z", "M13.5 3.5h7v7h-7z",
            "M3.5 13.5h7v7h-7z", "M13.5 13.5h7v7h-7z"
        ],
        "belt": [
            "M2.5 8.5h19v7h-19z", "M8 8.5v7", "M14 8.5v7",
            "M11 5.5v13"
        ],
        "deals": [
            "M12.5 3.5H20v7.5l-8.7 8.7a1.6 1.6 0 0 1-2.3 0l-5.2-5.2a1.6 1.6 0 0 1 0-2.3z",
            "M16.4 7.1h0.01"
        ],
        "history": [
            "M3.5 12a8.5 8.5 0 1 0 2.6-6.1", "M3.5 4.5V9H8",
            "M12 8v4.3l3 1.8"
        ],
        "diagnostics": ["M2.5 13h4l2.2-6 3.4 11 2.3-5h7.1"],
        "settings": [
            "M3.5 7h11", "M18.5 7h2", "M3.5 17h2", "M9.5 17h11",
            "M14.3 7a2.2 2.2 0 1 0 4.4 0 2.2 2.2 0 1 0-4.4 0",
            "M5.3 17a2.2 2.2 0 1 0 4.4 0 2.2 2.2 0 1 0-4.4 0"
        ],
        "discord": [
            "M4 5h16v11H9l-4.5 3v-3H4z", "M8 10h0.01", "M12 10h0.01",
            "M16 10h0.01"
        ],
        "bars": ["M4 19V11", "M10 19V5", "M16 19V8", "M22 19V3"],
        "book": [
            "M4 4.5h6.5A3.5 3.5 0 0 1 14 8v12H7.5A3.5 3.5 0 0 0 4 23z",
            "M20 4.5h-2.5A3.5 3.5 0 0 0 14 8v12h2.5A3.5 3.5 0 0 1 20 23z"
        ],
        "trend": ["M3 17l5-5 4 4 8-9", "M15 7h5v5"],
        "external": ["M9 5H5v14h14v-4", "M13 5h6v6", "M19 5l-9 9"],
        "clock": ["M12 5a8 8 0 1 1-8 8 8 8 0 0 1 8-8", "M9 2h6", "M12 9v4l3 2"],
        "warning": ["M12 3l9 17H3z", "M12 9v5", "M12 17h0.01"],
        "bell": ["M5 17h14l-2-3v-3a5 5 0 0 0-10 0v3z", "M10 20h4"],
        "monitor": ["M3 5h18v12H3z", "M9 21h6", "M12 17v4"],
        "info": ["M12 10v7", "M12 7h0.01"]
    })

    Shape {
        anchors.centerIn: parent
        width: 24
        height: 24
        scale: Math.min(root.width / 24, root.height / 24)
        antialiasing: true

        ShapePath {
            strokeColor: root.tint
            strokeWidth: root.weight
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin

            PathSvg {
                path: (root.paths[root.name] || []).join(" ")
            }
        }
    }
}
