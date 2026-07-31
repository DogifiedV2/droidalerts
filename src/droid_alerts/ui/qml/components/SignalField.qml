import QtQuick 2.15
import QtQuick.Controls 2.15
import DroidAlerts.Components 1.0

TextField {
    id: control
    implicitHeight: 34
    implicitWidth: 190
    leftPadding: 10
    rightPadding: 10
    color: Theme.ink
    placeholderTextColor: Theme.dim
    selectionColor: Theme.accent
    selectedTextColor: Theme.bg0
    font.family: Theme.bodyFont
    font.pixelSize: 12

    background: Rectangle {
        color: Theme.bg3
        border.color: control.activeFocus ? Theme.accent : Theme.line
        border.width: 1
        radius: 7
    }
}
