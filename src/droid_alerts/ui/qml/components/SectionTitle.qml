import QtQuick 2.15
import DroidAlerts.Components 1.0

Text {
    property string label: ""
    color: Theme.muted
    font.family: Theme.displayFont
    font.pixelSize: 11
    font.weight: Font.DemiBold
    font.letterSpacing: 1.7
    text: label.toUpperCase()
}
