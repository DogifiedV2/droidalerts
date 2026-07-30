import QtQuick 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

Rectangle {
    id: card
    property string title: ""
    property alias content: body.data
    default property alias cardData: body.data
    property int cardPadding: 16

    color: Theme.bg2
    border.color: Theme.line
    border.width: 1
    radius: 12
    implicitHeight: layout.implicitHeight + 2 * cardPadding

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: card.cardPadding
        spacing: card.title.length ? 12 : 0

        Text {
            visible: card.title.length > 0
            text: card.title.toUpperCase()
            color: Theme.muted
            font.family: Theme.displayFont
            font.pixelSize: 11
            font.weight: Font.DemiBold
            font.letterSpacing: 1.7
            Layout.fillWidth: true
        }

        ColumnLayout {
            id: body
            spacing: 10
            Layout.fillWidth: true
        }
    }
}
