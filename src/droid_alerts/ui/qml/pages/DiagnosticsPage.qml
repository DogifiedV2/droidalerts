import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

// Controllers are injected as context properties by application.py.
// qmllint disable unqualified

ScrollView {
    id: page
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    GridLayout {
        width: page.availableWidth
        columns: width >= 760 ? 2 : 1
        columnSpacing: 12
        rowSpacing: 12

        Item { Layout.preferredHeight: 2; Layout.columnSpan: parent.columns }

        SignalCard {
            title: "Chat Region"
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            Layout.leftMargin: 14
            Layout.rightMargin: parent.columns === 1 ? 14 : 0

            Text {
                text: "Confirm the capture box covers the Droid Tycoon chat messages."
                color: Theme.muted
                font.family: Theme.bodyFont
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            SignalButton {
                text: diagnosticsController.state.regionVisible
                      ? "Hide Chat Region" : "Show Chat Region"
                tone: "ghost"
                Layout.fillWidth: true
                onClicked: diagnosticsController.toggleRegion()
            }

            SignalButton {
                text: "Auto Detect Region"
                Layout.fillWidth: true
                onClicked: diagnosticsController.autoDetectRegion()
            }

            Text {
                text: diagnosticsController.state.regionStatus
                color: Theme.muted
                font.family: Theme.monoFont
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            GridLayout {
                id: regionNudgeGrid
                property int cellWidth: 78

                Layout.alignment: Qt.AlignHCenter
                columns: 3
                columnSpacing: 6
                rowSpacing: 6

                Item { Layout.preferredWidth: regionNudgeGrid.cellWidth }
                SignalButton {
                    text: "↑ Up"
                    compact: true
                    Layout.preferredWidth: regionNudgeGrid.cellWidth
                    enabled: diagnosticsController.state.regionVisible
                    onClicked: diagnosticsController.nudgeRegion(0, -10)
                }
                Item { Layout.preferredWidth: regionNudgeGrid.cellWidth }
                SignalButton {
                    text: "← Left"
                    compact: true
                    Layout.preferredWidth: regionNudgeGrid.cellWidth
                    enabled: diagnosticsController.state.regionVisible
                    onClicked: diagnosticsController.nudgeRegion(-10, 0)
                }
                Rectangle {
                    implicitWidth: regionNudgeGrid.cellWidth
                    implicitHeight: 30
                    color: Theme.bg3
                    radius: 7
                    Text {
                        anchors.centerIn: parent
                        text: "10 px"
                        color: Theme.muted
                        font.family: Theme.monoFont
                        font.pixelSize: 10
                    }
                }
                SignalButton {
                    text: "Right →"
                    compact: true
                    Layout.preferredWidth: regionNudgeGrid.cellWidth
                    enabled: diagnosticsController.state.regionVisible
                    onClicked: diagnosticsController.nudgeRegion(10, 0)
                }
                Item { Layout.preferredWidth: regionNudgeGrid.cellWidth }
                SignalButton {
                    text: "↓ Down"
                    compact: true
                    Layout.preferredWidth: regionNudgeGrid.cellWidth
                    enabled: diagnosticsController.state.regionVisible
                    onClicked: diagnosticsController.nudgeRegion(0, 10)
                }
                Item { Layout.preferredWidth: regionNudgeGrid.cellWidth }
            }
        }

        SignalCard {
            title: "Support & Storage"
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            Layout.leftMargin: parent.columns === 1 ? 14 : 0
            Layout.rightMargin: 14

            Text {
                text: diagnosticsController.state.storage
                color: Theme.muted
                font.family: Theme.bodyFont
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            SignalButton {
                text: diagnosticsController.state.busy
                      ? "Working…" : "Create Support Bundle"
                tone: "ghost"
                enabled: !diagnosticsController.state.busy
                Layout.fillWidth: true
                onClicked: diagnosticsController.createSupportBundle()
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 7
                rowSpacing: 7

                SignalButton {
                    text: "Check for Updates"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.checkUpdates()
                }
                SignalButton {
                    text: "Open Data Folder"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.openFolder("data")
                }
                SignalButton {
                    text: "Alert Samples"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.openFolder("samples")
                }
                SignalButton {
                    text: "Debug Captures"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.openFolder("debug")
                }
            }

            Text {
                visible: diagnosticsController.state.updateStatus.length > 0
                text: diagnosticsController.state.updateStatus
                color: Theme.accent
                font.family: Theme.bodyFont
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.25)
            }

            Text {
                text: "DANGER ZONE"
                color: Theme.danger
                font.family: Theme.displayFont
                font.pixelSize: 10
                font.weight: Font.DemiBold
                font.letterSpacing: 1.5
            }

            RowLayout {
                Layout.fillWidth: true
                SignalButton {
                    text: "Clear Debug Captures"
                    tone: "danger"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.clearDebug()
                }
                SignalButton {
                    text: "Clear History"
                    tone: "danger"
                    compact: true
                    Layout.fillWidth: true
                    onClicked: diagnosticsController.clearHistory()
                }
            }
        }

        Item {
            Layout.columnSpan: parent.columns
            Layout.preferredHeight: 2
        }
    }
}
