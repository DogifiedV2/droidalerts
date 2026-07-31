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
            title: "Current Limited Deal"
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Rectangle {
                    Layout.preferredWidth: 76
                    Layout.preferredHeight: 76
                    radius: 12
                    color: Theme.bg3
                    border.color: Theme.line
                    clip: true

                    Image {
                        anchors.fill: parent
                        anchors.margins: 3
                        source: dealsController.state.portrait
                        fillMode: Image.PreserveAspectFit
                        visible: source.toString().length > 0
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: dealsController.state.portrait.length === 0
                        text: "◉"
                        color: Theme.accent
                        font.pixelSize: 30
                    }
                }

                Text {
                    text: dealsController.state.offer
                    color: Theme.ink
                    font.family: Theme.bodyFont
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Text {
                    text: dealsController.state.countdown
                    color: Theme.warning
                    font.family: Theme.monoFont
                    font.pixelSize: 25
                    font.weight: Font.DemiBold
                }
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

                Text {
                    text: "Alert for these rarity and droid-class combos, regardless of the droid."
                    color: Theme.muted
                    font.family: Theme.bodyFont
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                GridLayout {
                    Layout.fillWidth: true
                    flow: GridLayout.TopToBottom
                    columns: width >= 420 ? 2 : 1
                    rows: width >= 420 ? 5 : 10
                    columnSpacing: 8
                    rowSpacing: 3

                    Repeater {
                        model: dealsController.state.priorityRows

                        SignalCheck {
                            required property var modelData
                            text: modelData.label
                            checked: modelData.enabled
                            Layout.fillWidth: true
                            onToggled: dealsController.setPriority(modelData.id, checked)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    SignalButton {
                        text: "Select all"
                        compact: true
                        onClicked: dealsController.selectAllPriorities(true)
                    }
                    SignalButton {
                        text: "Clear"
                        compact: true
                        onClicked: dealsController.selectAllPriorities(false)
                    }
                    Item { Layout.fillWidth: true }
                }
            }

            SignalCard {
                title: "Custom Droid Alerts"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: dealsController.state.targetCount + " custom target"
                              + (dealsController.state.targetCount === 1 ? "" : "s")
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 12
                        Layout.fillWidth: true
                    }
                    SignalButton {
                        text: "Modify"
                        tone: "ghost"
                        compact: true
                        onClicked: dealsController.chooseTargets()
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.max(56, customColumn.implicitHeight)
                    color: Theme.bg1
                    border.color: Theme.line
                    radius: 8
                    clip: true

                    ColumnLayout {
                        id: customColumn
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
                                spacing: 8
                                Repeater {
                                    model: [
                                        { text: "DROID", width: 1 },
                                        { text: "CLASS", width: 90 },
                                        { text: "ALERT FROM", width: 100 }
                                    ]
                                    Text {
                                        required property var modelData
                                        text: modelData.text
                                        color: Theme.muted
                                        font.family: Theme.displayFont
                                        font.pixelSize: 10
                                        font.letterSpacing: 1
                                        Layout.fillWidth: modelData.width === 1
                                        Layout.preferredWidth: modelData.width === 1 ? 20 : modelData.width
                                    }
                                }
                            }
                        }

                        Repeater {
                            model: dealsController.state.targets

                            RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.leftMargin: 10
                                Layout.rightMargin: 10
                                Layout.preferredHeight: 32
                                spacing: 8
                                Text {
                                    text: modelData.droid
                                    color: Theme.toneColor(modelData.tone)
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: modelData.droidClass
                                    color: Theme.muted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 11
                                    Layout.preferredWidth: 90
                                }
                                Text {
                                    text: modelData.minimum
                                    color: Theme.muted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 11
                                    Layout.preferredWidth: 100
                                }
                            }
                        }

                        Text {
                            visible: dealsController.state.targetCount === 0
                            text: "No custom droid rules"
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                            Layout.margins: 12
                        }
                    }
                }
            }
        }
    }
}
