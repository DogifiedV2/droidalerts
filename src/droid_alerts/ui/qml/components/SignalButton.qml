import QtQuick 2.15
import QtQuick.Controls 2.15
import DroidAlerts.Components 1.0

Button {
    id: control
    property string tone: "normal"
    property bool compact: false
    property bool defaultButton: false

    implicitHeight: compact ? 30 : 36
    implicitWidth: Math.max(compact ? 72 : 104, contentItem.implicitWidth + (compact ? 22 : 30))
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus

    contentItem: Text {
        text: control.text
        font.family: Theme.bodyFont
        font.pixelSize: control.compact ? 12 : 13
        font.weight: control.tone === "primary" ? Font.DemiBold : Font.Medium
        color: {
            if (!control.enabled) return Theme.dim
            if (control.tone === "primary") return Theme.bg0
            if (control.tone === "danger") return Theme.danger
            if (control.tone === "warning") return Theme.warning
            return Theme.ink
        }
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: 7
        color: {
            if (!control.enabled) return Theme.bg2
            if (control.tone === "primary") {
                return control.down ? Qt.darker(Theme.accent, 1.18) :
                       control.hovered ? Qt.lighter(Theme.accent, 1.08) : Theme.accent
            }
            if (control.down) return Theme.bg1
            if (control.hovered) return Theme.bgHover
            return control.tone === "ghost" ? "transparent" : Theme.bg3
        }
        border.width: control.activeFocus || control.defaultButton ? 2 : 1
        border.color: {
            if (!control.enabled) return Theme.lineSoft
            if (control.activeFocus || control.defaultButton) return Theme.accent
            if (control.tone === "primary") return Theme.accent
            if (control.tone === "danger") return Qt.rgba(0.94, 0.4, 0.45, 0.55)
            if (control.tone === "warning") return Qt.rgba(0.96, 0.73, 0.26, 0.55)
            return Theme.line
        }
    }
}
