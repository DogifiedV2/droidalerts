import QtQuick 2.15
import DroidAlerts.Components 1.0

Rectangle {
    property string tone: "muted"
    width: 8
    height: 8
    radius: 4
    color: Theme.toneColor(tone)
}
