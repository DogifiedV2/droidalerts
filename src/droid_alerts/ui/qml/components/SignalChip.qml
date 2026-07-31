import QtQuick 2.15
import DroidAlerts.Components 1.0

Rectangle {
    id: chip
    property string text: ""
    property string tone: "muted"
    property bool interactive: false
    signal clicked()

    function rainbowText(value) {
        var result = ""
        var colorIndex = 0
        for (var i = 0; i < value.length; ++i) {
            var character = value.charAt(i)
            if (character === " ") {
                result += "&#160;"
                continue
            }
            if (character === "&") character = "&amp;"
            else if (character === "<") character = "&lt;"
            else if (character === ">") character = "&gt;"
            var color = Theme.rainbowColors[colorIndex % Theme.rainbowColors.length]
            result += "<font color=\"" + color + "\">" + character + "</font>"
            colorIndex += 1
        }
        return result
    }

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
        text: chip.tone === "rainbow" ? chip.rainbowText(chip.text) : chip.text
        textFormat: chip.tone === "rainbow" ? Text.StyledText : Text.PlainText
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
