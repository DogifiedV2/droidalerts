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

    Item {
        width: page.availableWidth
        implicitHeight: grid.implicitHeight + 28

        GridLayout {
            id: grid
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            columns: width >= 820 ? 2 : 1
            columnSpacing: 12
            rowSpacing: 12

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 12

                SignalCard {
                    title: "Monitoring"
                    Layout.fillWidth: true

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Text {
                                text: dashboardController.state.watcherTitle
                                color: Theme.ink
                                font.family: Theme.bodyFont
                                font.pixelSize: 18
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: dashboardController.state.watcherDetail
                                color: Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }

                        StatusLamp {
                            tone: dashboardController.state.statusTone
                            Layout.preferredWidth: 10
                            Layout.preferredHeight: 10
                            radius: 5
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: sourceColumn.implicitHeight + 20
                        radius: 8
                        color: Theme.bg3
                        border.color: Theme.line

                        ColumnLayout {
                            id: sourceColumn
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 8

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Text {
                                    text: "▣"
                                    color: Theme.accent
                                    font.pixelSize: 16
                                }
                                Text {
                                    text: captureController.state.sourceLabel
                                    color: Theme.ink
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 7

                                SignalCombo {
                                    model: captureController.state.monitors
                                    valueRoleName: "key"
                                    currentIndex: Math.max(
                                                      0,
                                                      indexOfValue(
                                                          captureController.state.monitorKey))
                                    Layout.fillWidth: true
                                    onActivated: captureController.selectMonitor(currentValue)
                                }

                                Item {
                                    Layout.preferredWidth: windowButton.implicitWidth
                                    Layout.preferredHeight: windowButton.implicitHeight

                                    SignalButton {
                                        id: windowButton
                                        anchors.fill: parent
                                        text: "Window"
                                        compact: true
                                        enabled: captureController.state.windowCaptureAvailable
                                                 && !captureController.state.busy
                                        onClicked: captureController.chooseWindow()
                                    }

                                    MouseArea {
                                        id: windowUnavailableHover
                                        anchors.fill: parent
                                        enabled: !captureController.state.windowCaptureAvailable
                                        hoverEnabled: true
                                        acceptedButtons: Qt.NoButton
                                    }

                                    ToolTip.visible: windowUnavailableHover.containsMouse
                                    ToolTip.text: captureController.state.windowCaptureUnavailableReason
                                    ToolTip.delay: 250
                                }

                                Item {
                                    Layout.preferredWidth: deviceButton.implicitWidth
                                    Layout.preferredHeight: deviceButton.implicitHeight

                                    SignalButton {
                                        id: deviceButton
                                        anchors.fill: parent
                                        text: "Device"
                                        compact: true
                                        enabled: captureController.state.deviceCaptureAvailable
                                                 && !captureController.state.busy
                                        onClicked: captureController.chooseDevice()
                                    }

                                    MouseArea {
                                        id: deviceUnavailableHover
                                        anchors.fill: parent
                                        enabled: !captureController.state.deviceCaptureAvailable
                                        hoverEnabled: true
                                        acceptedButtons: Qt.NoButton
                                    }

                                    ToolTip.visible: deviceUnavailableHover.containsMouse
                                    ToolTip.text: captureController.state.deviceCaptureUnavailableReason
                                    ToolTip.delay: 250
                                }
                            }
                        }
                    }
                }

                SignalCard {
                    title: "Priority Alerts"
                    Layout.fillWidth: true

                    Flow {
                        Layout.fillWidth: true
                        spacing: 7

                        Repeater {
                            model: dashboardController.state.priorities
                            SignalChip {
                                required property var modelData
                                text: modelData.label
                                tone: modelData.tone
                            }
                        }

                        Text {
                            visible: dashboardController.state.priorityCount === 0
                            text: "No chat priorities selected"
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: dashboardController.state.priorityCount
                                  + " chat alert"
                                  + (dashboardController.state.priorityCount === 1 ? "" : "s")
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        SignalButton {
                            text: "Modify"
                            tone: "ghost"
                            compact: true
                            onClicked: dashboardController.choosePriorities()
                        }
                    }
                }

                SignalCard {
                    title: "Alert Channels"
                    Layout.fillWidth: true

                    Repeater {
                        model: dashboardController.state.channels

                        ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            spacing: 6

                            Rectangle {
                                visible: index > 0
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                color: Theme.lineSoft
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                SignalCheck {
                                    text: modelData.label
                                    checked: modelData.enabled
                                    implicitWidth: 112
                                    onToggled: dashboardController.setChannelEnabled(
                                                   modelData.id, checked)
                                }

                                Text {
                                    text: modelData.detail
                                    color: modelData.configured ? Theme.good : Theme.muted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                SignalButton {
                                    text: "Configure"
                                    tone: "ghost"
                                    compact: true
                                    onClicked: dashboardController.configureChannel(modelData.id)
                                }

                                SignalButton {
                                    visible: modelData.id === "popup"
                                    text: "Position & Size"
                                    tone: "ghost"
                                    compact: true
                                    onClicked: dashboardController.adjustPopup()
                                }

                                SignalButton {
                                    text: "Test"
                                    compact: true
                                    onClicked: dashboardController.testChannel(modelData.id)
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        Layout.topMargin: 4
                        color: Theme.lineSoft
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 560 ? 3 : 1
                        columnSpacing: 7
                        rowSpacing: 7

                        SignalButton {
                            text: "Sounds by Alert"
                            tone: "ghost"
                            compact: true
                            Layout.fillWidth: true
                            onClicked: settingsController.configureAlertSounds()
                        }
                        SignalButton {
                            text: "Discord Webhooks"
                            tone: "ghost"
                            compact: true
                            Layout.fillWidth: true
                            onClicked: settingsController.configureDiscordRoutes()
                        }
                        SignalButton {
                            text: "Discord Messages"
                            tone: "ghost"
                            compact: true
                            Layout.fillWidth: true
                            onClicked: settingsController.configureDiscordMessages()
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 12

                SignalCard {
                    title: "Next Spawns"
                    Layout.fillWidth: true

                    Repeater {
                        model: dashboardController.state.timers

                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 9

                            Text {
                                text: modelData.label
                                color: modelData.color
                                font.family: Theme.displayFont
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                font.letterSpacing: 1.1
                                Layout.preferredWidth: 68
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 5
                                radius: 3
                                color: Theme.bg3
                                clip: true

                                Rectangle {
                                    height: parent.height
                                    width: parent.width * modelData.progress
                                    radius: parent.radius
                                    color: modelData.hot ? Theme.warning : modelData.color
                                }
                            }

                            Text {
                                text: modelData.countdown
                                color: modelData.hot ? Theme.warning : Theme.ink
                                font.family: Theme.monoFont
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                                horizontalAlignment: Text.AlignRight
                                Layout.preferredWidth: 60

                                ToolTip.visible: timerMouse.containsMouse
                                ToolTip.text: modelData.target
                                MouseArea {
                                    id: timerMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        SignalCheck {
                            text: "In-game overlay"
                            checked: dashboardController.state.timersEnabled
                            onToggled: dashboardController.setTimersEnabled(checked)
                        }
                        Item { Layout.fillWidth: true }
                        SignalButton {
                            text: "Position"
                            tone: "ghost"
                            compact: true
                            enabled: dashboardController.state.timersEnabled
                            onClicked: dashboardController.adjustTimers()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: dashboardController.state.timersEnabled
                        SignalCheck {
                            text: "Timer Reminder"
                            checked: dashboardController.state.timerReminders
                            onToggled: dashboardController.setTimerRemindersEnabled(checked)
                        }
                        Item { Layout.fillWidth: true }
                        SignalButton {
                            text: "Configure"
                            tone: "ghost"
                            compact: true
                            onClicked: dashboardController.configureTimerReminders()
                        }
                    }
                }

                SignalCard {
                    title: "Session"
                    Layout.fillWidth: true

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Repeater {
                            model: [
                                { value: dashboardController.state.scans, label: "scans" },
                                { value: dashboardController.state.alerts, label: "alerts" },
                                { value: dashboardController.state.uptime, label: "uptime" }
                            ]

                            Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 66
                                radius: 8
                                color: Theme.bg3
                                border.color: Theme.line

                                Column {
                                    anchors.centerIn: parent
                                    spacing: 2
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: modelData.value
                                        color: Theme.ink
                                        font.family: Theme.monoFont
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: modelData.label.toUpperCase()
                                        color: Theme.muted
                                        font.family: Theme.bodyFont
                                        font.pixelSize: 9
                                        font.letterSpacing: 1
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        text: "Last alert · " + dashboardController.state.lastAlert
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    SignalButton {
                        text: "Test All Alerts"
                        tone: "ghost"
                        Layout.fillWidth: true
                        onClicked: dashboardController.testAllAlerts()
                    }
                }

                SignalCard {
                    title: "Special Alerts"
                    Layout.fillWidth: true

                    SignalCheck {
                        text: "Rebirth Ready"
                        checked: settingsController.state.values.rebirth_ready_alert_enabled
                        onToggled: settingsController.setValue(
                                       "rebirth_ready_alert_enabled", checked)
                    }
                    SignalCheck {
                        text: "Scrap Alert"
                        checked: settingsController.state.values.scrap_alert_enabled
                        onToggled: settingsController.setValue("scrap_alert_enabled", checked)
                    }
                    SignalCheck {
                        text: "Rebirth droid available"
                        checked: settingsController.state.values.rebirth_alert_enabled
                        onToggled: settingsController.setValue(
                                       "rebirth_alert_enabled", checked)
                    }
                    SignalCheck {
                        text: "CB23 Mission"
                        checked: settingsController.state.values.cb23_mission_alert_enabled
                        onToggled: settingsController.setValue(
                                       "cb23_mission_alert_enabled", checked)
                    }
                }
            }
        }
    }
}
