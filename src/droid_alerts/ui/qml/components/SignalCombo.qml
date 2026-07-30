pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import DroidAlerts.Components 1.0

ComboBox {
    id: control
    property string textRoleName: "label"
    property string valueRoleName: "id"

    textRole: textRoleName
    valueRole: valueRoleName
    implicitHeight: 34
    implicitWidth: 190
    leftPadding: 11
    rightPadding: 30
    font.family: Theme.bodyFont
    font.pixelSize: 12

    contentItem: Text {
        text: control.displayText
        color: control.enabled ? Theme.ink : Theme.dim
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        x: control.width - width - 10
        y: (control.height - height) / 2 - 1
        text: "▾"
        color: Theme.muted
        font.pixelSize: 13
    }

    background: Rectangle {
        color: Theme.bg3
        border.color: control.activeFocus ? Theme.accent : Theme.line
        border.width: 1
        radius: 7
    }

    popup: Popup {
        y: control.height + 3
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
        padding: 4

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            color: Theme.bg2
            border.color: Theme.line
            radius: 8
        }
    }

    delegate: ItemDelegate {
        id: delegateItem
        required property int index
        required property var modelData
        width: control.width - 8
        height: 32
        highlighted: control.highlightedIndex === delegateItem.index
        contentItem: Text {
            text: {
                var value = delegateItem.modelData
                if (typeof value === "object" && value !== null)
                    return value[control.textRoleName]
                return String(value)
            }
            color: Theme.ink
            font.family: Theme.bodyFont
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 5
            color: delegateItem.highlighted ? Theme.accentSoft : "transparent"
        }
    }
}
