import QtQuick 2.15
import QtQuick.Controls 2.15
import DroidAlerts.Components 1.0

CheckBox {
    id: control
    property bool large: false

    spacing: 9
    implicitHeight: large ? 34 : 28
    hoverEnabled: true

    indicator: Rectangle {
        implicitWidth: control.large ? 38 : 32
        implicitHeight: control.large ? 21 : 18
        x: 0
        y: (control.height - height) / 2
        radius: height / 2
        color: control.checked ? Theme.accentSoft : Theme.bg3
        border.color: control.checked ? Theme.accent : Theme.line
        border.width: 1

        Rectangle {
            width: parent.height - 6
            height: width
            radius: width / 2
            y: 3
            x: control.checked ? parent.width - width - 3 : 3
            color: control.checked ? Theme.accent : Theme.muted
            Behavior on x {
                NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
            }
        }
    }

    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        color: control.enabled ? Theme.ink : Theme.dim
        font.family: Theme.bodyFont
        font.pixelSize: control.large ? 14 : 13
        font.weight: control.large ? Font.DemiBold : Font.Normal
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
}
