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

    ColumnLayout {
        width: page.availableWidth
        spacing: 12

        Item { Layout.preferredHeight: 2 }

        SignalCard {
            title: "Tracking"
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text {
                        text: beltController.state.title
                        color: Theme.ink
                        font.family: Theme.bodyFont
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: beltController.state.detail
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }

                StatusLamp {
                    tone: beltController.state.statusTone
                    Layout.preferredWidth: 10
                    Layout.preferredHeight: 10
                    radius: 5
                }

                SignalButton {
                    text: "FAQ"
                    compact: true
                    onClicked: beltController.showFaq()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                SignalButton {
                    text: beltController.state.buttonText
                    tone: beltController.state.tracking ? "danger" : "primary"
                    onClicked: beltController.toggleTracking()
                }
                SignalButton {
                    text: "Select Belt Region"
                    tone: "ghost"
                    enabled: beltController.state.controlsEnabled
                    onClicked: beltController.selectRegion()
                }
                Item { Layout.fillWidth: true }
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.bottomMargin: 14
            columns: width >= 760 ? 2 : 1
            columnSpacing: 12
            rowSpacing: 12

            SignalCard {
                title: "Priority Alerts"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: beltController.state.targetCount + " belt target"
                              + (beltController.state.targetCount === 1 ? "" : "s")
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 12
                        Layout.fillWidth: true
                    }
                    SignalButton {
                        text: "Modify"
                        tone: "ghost"
                        compact: true
                        enabled: beltController.state.controlsEnabled
                        onClicked: beltController.chooseTargets()
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.max(56, targetColumn.implicitHeight)
                    color: Theme.bg1
                    border.color: Theme.line
                    radius: 8
                    clip: true

                    ColumnLayout {
                        id: targetColumn
                        width: parent.width
                        spacing: 0

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 30
                            color: Theme.bg3
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                Text {
                                    text: "DROID"
                                    color: Theme.muted
                                    font.family: Theme.displayFont
                                    font.pixelSize: 10
                                    font.letterSpacing: 1.2
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: "ALERT FROM"
                                    color: Theme.muted
                                    font.family: Theme.displayFont
                                    font.pixelSize: 10
                                    font.letterSpacing: 1.2
                                    Layout.preferredWidth: 110
                                }
                            }
                        }

                        Repeater {
                            model: beltController.state.targets

                            Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                height: 32
                                color: "transparent"
                                border.color: Theme.lineSoft
                                border.width: index > 0 ? 1 : 0
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    Text {
                                        text: modelData.droid
                                        color: Theme.toneColor(modelData.tone)
                                        font.family: Theme.bodyFont
                                        font.pixelSize: 12
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: modelData.minimum
                                        color: Theme.muted
                                        font.family: Theme.bodyFont
                                        font.pixelSize: 12
                                        Layout.preferredWidth: 110
                                    }
                                }
                            }
                        }

                        Text {
                            visible: beltController.state.targetCount === 0
                            text: "No belt alert rules"
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                            Layout.margins: 12
                        }
                    }
                }
            }

            SignalCard {
                title: "Belt Area"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop

                Text {
                    text: beltController.state.regionLabel
                    color: Theme.ink
                    font.family: Theme.monoFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }

                SignalCheck {
                    text: "Show belt overlay"
                    checked: beltController.state.overlayEnabled
                    onToggled: beltController.setOverlayEnabled(checked)
                }

                Text {
                    text: beltController.state.trackLabel
                    color: Theme.accent
                    font.family: Theme.bodyFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }

                Text {
                    text: beltController.state.lastScan
                    color: Theme.muted
                    font.family: Theme.bodyFont
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Text {
                    visible: settingsController.state.values.belt_template_collection_enabled
                    text: beltController.state.sampleStatus
                    color: Theme.muted
                    font.family: Theme.bodyFont
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }
}
