import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

// Controllers are injected as context properties by application.py.
// qmllint disable unqualified

Item {
    id: page

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        SignalCard {
            Layout.fillWidth: true
            cardPadding: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 7

                Flow {
                    Layout.fillWidth: true
                    spacing: 6

                    Repeater {
                        model: historyController.state.filters

                        SignalChip {
                            required property var modelData
                            text: modelData.label
                            tone: historyController.state.activeFilter === modelData.id
                                  ? "accent" : "muted"
                            interactive: true
                            onClicked: historyController.setFilter(modelData.id)
                        }
                    }
                }

                SignalField {
                    placeholderText: "Search droid, rarity…"
                    implicitWidth: 190
                    text: historyController.state.search
                    onTextEdited: historyController.setSearch(text)
                }
            }

            Text {
                text: historyController.state.summary
                color: Theme.muted
                font.family: Theme.bodyFont
                font.pixelSize: 11
                Layout.alignment: Qt.AlignRight
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.bg1
            border.color: Theme.line
            radius: 9
            clip: true

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    color: Theme.bg3

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        spacing: 8

                        Repeater {
                            model: [
                                { text: "TIME", width: 132 },
                                { text: "EVENT", width: 108 },
                                { text: "DROID", width: 160 },
                                { text: "RARITY", width: 110 },
                                { text: "STATUS", width: 82 },
                                { text: "DETAILS", fill: true }
                            ]

                            Text {
                                required property var modelData
                                text: modelData.text
                                color: Theme.muted
                                font.family: Theme.displayFont
                                font.pixelSize: 10
                                font.letterSpacing: 1.2
                                Layout.fillWidth: Boolean(modelData.fill)
                                Layout.preferredWidth: modelData.width || 80
                            }
                        }
                    }
                }

                ListView {
                    id: historyList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: historyController.state.rows
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    delegate: Rectangle {
                        required property var modelData
                        width: historyList.width
                        height: 38
                        color: rowMouse.containsMouse ? Theme.bgHover : "transparent"

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 1
                            color: Theme.lineSoft
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 8

                            Text {
                                text: modelData.time
                                color: Theme.muted
                                font.family: Theme.monoFont
                                font.pixelSize: 11
                                Layout.preferredWidth: 132
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.event
                                color: modelData.status === "Alerted" ? Theme.accent : Theme.ink
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                                Layout.preferredWidth: 108
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.droid
                                color: Theme.ink
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                                Layout.preferredWidth: 160
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.rarity
                                color: modelData.rarity.toLowerCase().indexOf("galactic") >= 0
                                       ? Theme.galactic
                                       : modelData.rarity.toLowerCase().indexOf("mythic") >= 0
                                         ? Theme.mythic : Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                                Layout.preferredWidth: 110
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.status
                                color: Theme.toneColor(modelData.tone)
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                                Layout.preferredWidth: 82
                                elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.detail
                                color: Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onDoubleClicked: historyController.showDetails(modelData.id)
                        }
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: historyList.count === 0
                        text: "No matching history"
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 13
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            SignalButton {
                text: "Refresh"
                compact: true
                onClicked: historyController.refresh()
            }
            SignalButton {
                text: "Export CSV"
                compact: true
                onClicked: historyController.exportCsv()
            }
            SignalButton {
                text: "Open Logs Folder"
                compact: true
                onClicked: historyController.openLogs()
            }
        }
    }
}
