import QtQuick 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

RowLayout {
    id: row
    property string label: ""
    property string settingKey: ""
    property var value: ""
    property string suffix: ""
    signal submitted(var value)

    spacing: 10
    Layout.fillWidth: true

    Text {
        text: row.label
        color: Theme.muted
        font.family: Theme.bodyFont
        font.pixelSize: 12
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }

    SignalField {
        text: String(row.value)
        implicitWidth: 150
        onEditingFinished: row.submitted(text)
    }

    Text {
        visible: row.suffix.length > 0
        text: row.suffix
        color: Theme.muted
        font.family: Theme.bodyFont
        font.pixelSize: 11
        Layout.preferredWidth: 34
    }
}
