import QtQuick 2.15
import DroidAlerts.Components 1.0

Rectangle {
    id: chip
    property alias text: label.text
    property string tone: "muted"
    property bool interactive: false
    signal clicked()

    implicitWidth: label.implicitWidth + 20
    implicitHeight: 27
    radius: 6
    color: mouse.containsMouse && interactive ? Theme.bgHover : Theme.bg3
    border.width: 1
    border.color: Qt.rgba(
        Theme.toneColor(tone).r,
        Theme.toneColor(tone).g,
        Theme.toneColor(tone).b,
        tone === "muted" ? 0.35 : 0.55
    )

    Text {
        id: label
        anchors.centerIn: parent
        color: Theme.toneColor(chip.tone)
        font.family: Theme.bodyFont
        font.pixelSize: 11
        font.weight: Font.Medium
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: chip.interactive
        hoverEnabled: true
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: chip.clicked()
    }
}
