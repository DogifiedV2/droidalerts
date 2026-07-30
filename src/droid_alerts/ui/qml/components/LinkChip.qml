import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

Button {
    id: control
    property string label: ""
    property string iconName: ""
    property string destination: ""
    signal activated()

    implicitHeight: 30
    implicitWidth: 96
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    onClicked: activated()

    ToolTip.visible: hovered
    ToolTip.text: destination.length ? "Opens " + destination : "Opens in your browser"
    ToolTip.delay: 450

    contentItem: RowLayout {
        spacing: 7

        NavIcon {
            name: control.iconName
            tint: control.hovered ? Theme.accent : Theme.muted
            Layout.leftMargin: 8
            Layout.preferredWidth: 15
            Layout.preferredHeight: 15
        }

        Text {
            text: control.label
            color: control.hovered ? Theme.ink : Theme.muted
            font.family: Theme.bodyFont
            font.pixelSize: 11
            font.weight: Font.Medium
            Layout.fillWidth: true
            elide: Text.ElideRight
        }

        NavIcon {
            name: "external"
            tint: control.hovered ? Theme.accent : Theme.dim
            Layout.rightMargin: 7
            Layout.preferredWidth: 11
            Layout.preferredHeight: 11
        }
    }

    background: Rectangle {
        radius: 6
        color: control.down ? Theme.bg1
                            : control.hovered ? Theme.accentSoft : Theme.bg2
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus || control.hovered ? Theme.accent : Theme.line
    }
}
