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

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 10

            Text {
                text: "Changes save automatically."
                color: Theme.muted
                font.family: Theme.bodyFont
                font.pixelSize: 12
                Layout.fillWidth: true
            }

            SignalCheck {
                text: "Advanced settings"
                large: true
                checked: settingsController.state.advanced
                onToggled: settingsController.setValue("advanced_mode", checked)
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            columns: width >= 760 ? 2 : 1
            columnSpacing: 12
            rowSpacing: 12

            SignalCard {
                title: "Everyday Behaviour"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop

                SignalCheck {
                    text: "Start watching when Droid Alerts opens"
                    checked: settingsController.state.values.start_watcher_on_launch
                    onToggled: settingsController.setValue(
                                   "start_watcher_on_launch", checked)
                    Layout.fillWidth: true
                }
                SignalCheck {
                    text: "Check for updates automatically"
                    checked: settingsController.state.values.update_check_enabled
                    onToggled: settingsController.setValue("update_check_enabled", checked)
                    Layout.fillWidth: true
                }
            }

            SignalCard {
                title: "Notification Profiles"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop

                Text {
                    text: "Active · " + settingsController.state.activeProfile
                    color: Theme.muted
                    font.family: Theme.bodyFont
                    font.pixelSize: 12
                }
                SignalCombo {
                    model: settingsController.state.profileChoices
                    enabled: count > 0
                    Layout.fillWidth: true
                    onActivated: {
                        if (currentValue)
                            settingsController.activateNotificationProfile(currentValue)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    SignalButton {
                        text: "Save current"
                        compact: true
                        Layout.fillWidth: true
                        onClicked: settingsController.saveNotificationProfile()
                    }
                    SignalButton {
                        text: "Delete active"
                        tone: "ghost"
                        compact: true
                        enabled: settingsController.state.activeProfile !== "No active profile"
                        onClicked: settingsController.deleteNotificationProfile()
                    }
                }
            }

            SignalCard {
                title: "Quiet Hours & Snooze"
                Layout.fillWidth: true
                Layout.columnSpan: parent.columns

                Text {
                    text: "Snooze temporarily mutes the checked channels, even outside scheduled quiet hours. Bypassed alerts still notify."
                    color: Theme.muted
                    font.family: Theme.bodyFont
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                SignalCheck {
                    text: "Enable quiet hours"
                    checked: settingsController.state.values.quiet_hours_enabled
                    onToggled: settingsController.setValue("quiet_hours_enabled", checked)
                }
                RowLayout {
                    Layout.fillWidth: true
                    SettingField {
                        label: "Start (24-hour)"
                        value: settingsController.state.values.quiet_hours_start
                        Layout.fillWidth: true
                        onSubmitted: function(value) {
                            settingsController.setValue("quiet_hours_start", value)
                        }
                    }
                    SettingField {
                        label: "End (24-hour)"
                        value: settingsController.state.values.quiet_hours_end
                        Layout.fillWidth: true
                        onSubmitted: function(value) {
                            settingsController.setValue("quiet_hours_end", value)
                        }
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 5
                    Repeater {
                        model: [
                            { id: "popup", label: "Popup" },
                            { id: "sound", label: "Sound" },
                            { id: "discord", label: "Discord" },
                            { id: "ntfy", label: "ntfy" },
                            { id: "pushover", label: "Pushover" }
                        ]
                        SignalCheck {
                            required property var modelData
                            text: modelData.label
                            checked: Boolean(settingsController.state.quietChannels[modelData.id])
                            onToggled: settingsController.setQuietChannel(modelData.id, checked)
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: settingsController.state.snoozeStatus
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 12
                        Layout.fillWidth: true
                    }
                    SignalButton {
                        text: "Pause 30 min"
                        compact: true
                        onClicked: settingsController.snoozeNotifications(30)
                    }
                    SignalButton {
                        text: "Pause 1 hour"
                        compact: true
                        onClicked: settingsController.snoozeNotifications(60)
                    }
                    SignalButton {
                        text: "Resume"
                        tone: "ghost"
                        compact: true
                        onClicked: settingsController.snoozeNotifications(0)
                    }
                    SignalButton {
                        text: "Bypasses"
                        tone: "ghost"
                        compact: true
                        onClicked: settingsController.configureQuietBypass()
                    }
                }
            }

            SignalCard {
                title: "Help & Privacy"
                Layout.fillWidth: true
                Layout.columnSpan: parent.columns

                GridLayout {
                    Layout.fillWidth: true
                    columns: width >= 680 ? 4 : 2
                    columnSpacing: 7
                    rowSpacing: 7
                    SignalButton {
                        text: "What is shared?"
                        compact: true
                        Layout.fillWidth: true
                        onClicked: settingsController.showPrivacy()
                    }
                    SignalButton {
                        text: "Identify This Install"
                        compact: true
                        Layout.fillWidth: true
                        onClicked: settingsController.identifyInstall()
                    }
                    SignalButton {
                        text: "FAQ"
                        compact: true
                        Layout.fillWidth: true
                        onClicked: settingsController.showFaq()
                    }
                    SignalButton {
                        text: "Discord & Support"
                        compact: true
                        Layout.fillWidth: true
                        onClicked: settingsController.openDiscord()
                    }
                }
            }
        }

        ColumnLayout {
            visible: settingsController.state.advanced
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.bottomMargin: 14
            spacing: 12

            GridLayout {
                Layout.fillWidth: true
                columns: width >= 760 ? 2 : 1
                columnSpacing: 12
                rowSpacing: 12

                SignalCard {
                    title: "Alert Appearance"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop

                    SettingField {
                        label: "Popup duration"
                        value: settingsController.state.values.popup_seconds
                        suffix: "sec"
                        onSubmitted: function(value) {
                            settingsController.setValue("popup_seconds", value)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Popup position"
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        SignalCombo {
                            model: settingsController.state.popupPositions
                            currentIndex: Math.max(
                                              0,
                                              indexOfValue(
                                                  settingsController.state.values.popup_position))
                            implicitWidth: 150
                            onActivated: settingsController.setValue(
                                             "popup_position", currentValue)
                        }
                    }

                    SettingField {
                        label: "Popup size"
                        value: settingsController.state.values.popup_scale
                        suffix: "×"
                        onSubmitted: function(value) {
                            settingsController.setValue("popup_scale", value)
                        }
                    }
                    SettingField {
                        label: "Popup opacity"
                        value: settingsController.state.values.popup_opacity
                        onSubmitted: function(value) {
                            settingsController.setValue("popup_opacity", value)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "Alert sound"
                            color: Theme.muted
                            font.family: Theme.bodyFont
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        SignalCombo {
                            model: settingsController.state.soundChoices
                            currentIndex: {
                                var wanted = settingsController.state.values.sound_file
                                             || "System beeps"
                                return Math.max(0, indexOfValue(wanted))
                            }
                            implicitWidth: 170
                            onActivated: settingsController.setValue(
                                             "sound_file", currentValue)
                        }
                        SignalButton {
                            text: "Add WAV"
                            compact: true
                            onClicked: settingsController.addAlertSound()
                        }
                    }
                }

                SignalCard {
                    title: "Wake-Up Alarm"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop

                    SignalCheck {
                        text: "WAKE ME UP AT ALL COSTS"
                        large: true
                        checked: settingsController.state.values.wake_alarm_enabled
                        onToggled: settingsController.setValue(
                                       "wake_alarm_enabled", checked)
                    }

                    Text {
                        text: "Loops a loud alarm for up to 40 seconds. This is separate from the normal Sound toggle."
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    SignalCheck {
                        text: "Beskar Mythic"
                        checked: settingsController.state.values.wake_alarm_beskar_mythic
                        onToggled: settingsController.setValue(
                                       "wake_alarm_beskar_mythic", checked)
                    }
                    SignalCheck {
                        text: "Galactic Mythic"
                        checked: settingsController.state.values.wake_alarm_galactic_mythic
                        onToggled: settingsController.setValue(
                                       "wake_alarm_galactic_mythic", checked)
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        SignalButton {
                            text: "Test for 3 Seconds"
                            tone: "warning"
                            compact: true
                            onClicked: settingsController.testWakeAlarm()
                        }
                        SignalButton {
                            text: "STOP ALARM"
                            tone: "danger"
                            compact: true
                            onClicked: settingsController.stopWakeAlarm()
                        }
                    }
                }
            }

            SignalCard {
                title: "Detection & Timing"
                Layout.fillWidth: true

                GridLayout {
                    Layout.fillWidth: true
                    columns: width >= 700 ? 2 : 1
                    columnSpacing: 24
                    rowSpacing: 7

                    Repeater {
                        model: [
                            { label: "Capture interval", key: "capture_interval_seconds", suffix: "sec" },
                            { label: "Rebirth scan interval", key: "rebirth_scan_interval_seconds", suffix: "sec" },
                            { label: "Duplicate window", key: "dedupe_seconds", suffix: "sec" },
                            { label: "Alert cooldown", key: "alert_cooldown_seconds", suffix: "sec" },
                            { label: "Calibration warning frames", key: "validation_failures_before_calibration_prompt", suffix: "" },
                            { label: "Timer schedule offset", key: "timer_offset_seconds", suffix: "sec" },
                            { label: "Belt idle scan rate", key: "belt_idle_scan_fps", suffix: "FPS" },
                            { label: "Belt active scan rate", key: "belt_active_scan_fps", suffix: "FPS" }
                        ]

                        SettingField {
                            required property var modelData
                            label: modelData.label
                            value: settingsController.state.values[modelData.key]
                            suffix: modelData.suffix
                            onSubmitted: function(value) {
                                settingsController.setValue(modelData.key, value)
                            }
                        }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: width >= 760 ? 2 : 1
                columnSpacing: 12
                rowSpacing: 12

                SignalCard {
                    title: "Storage & Debug"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop

                    SignalCheck {
                        text: "Save alert screenshots"
                        checked: settingsController.state.values.save_alert_samples
                        onToggled: settingsController.setValue(
                                       "save_alert_samples", checked)
                    }
                    SignalCheck {
                        text: "Save debug captures"
                        checked: settingsController.state.values.save_debug_screenshots
                        onToggled: settingsController.setValue(
                                       "save_debug_screenshots", checked)
                    }
                    SignalCheck {
                        text: "Share alert debug screenshots with the developer"
                        enabled: settingsController.state.values.save_debug_screenshots
                        checked: settingsController.state.values.share_debug_detections
                        onToggled: settingsController.setValue(
                                       "share_debug_detections", checked)
                    }
                    SettingField {
                        label: "Delete captures after"
                        value: settingsController.state.values.retention_days
                        suffix: "days"
                        onSubmitted: function(value) {
                            settingsController.setValue("retention_days", value)
                        }
                    }
                    SettingField {
                        label: "Storage limit"
                        value: settingsController.state.values.max_storage_mb
                        suffix: "MB"
                        onSubmitted: function(value) {
                            settingsController.setValue("max_storage_mb", value)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        SignalButton {
                            text: "Refresh Display Layout"
                            compact: true
                            onClicked: settingsController.refreshDisplayLayout()
                        }
                        SignalButton {
                            text: "Identify Displays"
                            compact: true
                            onClicked: settingsController.identifyDisplays()
                        }
                    }
                }

                SignalCard {
                    title: "Notification Details"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop

                    Repeater {
                        model: [
                            { label: "ntfy server", key: "ntfy_server_url" },
                            { label: "ntfy topic", key: "ntfy_topic" },
                            { label: "ntfy priority", key: "ntfy_priority" },
                            { label: "ntfy tags", key: "ntfy_tags" },
                            { label: "Pushover sound", key: "phone_sound" },
                            { label: "Release repository", key: "update_repo" }
                        ]
                        SettingField {
                            required property var modelData
                            label: modelData.label
                            value: settingsController.state.values[modelData.key]
                            onSubmitted: function(value) {
                                settingsController.setValue(modelData.key, value)
                            }
                        }
                    }

                    SignalCheck {
                        text: "Attach screenshot to ntfy"
                        checked: settingsController.state.values.ntfy_include_attachment
                        onToggled: settingsController.setValue(
                                       "ntfy_include_attachment", checked)
                    }
                    SignalCheck {
                        text: "Attach screenshot to Pushover"
                        checked: settingsController.state.values.phone_include_attachment
                        onToggled: settingsController.setValue(
                                       "phone_include_attachment", checked)
                    }
                    SignalButton {
                        text: "Open Config"
                        compact: true
                        onClicked: settingsController.openPath("config")
                    }
                }
            }

            SignalCard {
                title: "Blueprint Collection"
                Layout.fillWidth: true

                GridLayout {
                    Layout.fillWidth: true
                    columns: width >= 620 ? 2 : 1
                    columnSpacing: 18
                    rowSpacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        SignalCheck {
                            text: "Blueprint collection mode — Windows: press P to capture"
                            checked: settingsController.state.values.belt_dev_mode
                            onToggled: settingsController.setValue("belt_dev_mode", checked)
                            Layout.fillWidth: true
                        }
                        SignalButton {
                            text: "Open Collection"
                            compact: true
                            onClicked: settingsController.openPath("belt_logs")
                        }
                        SignalButton {
                            text: "Export ZIP"
                            compact: true
                            onClicked: settingsController.exportBeltCollection()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        SignalCheck {
                            text: "Save confirmed detections"
                            checked: settingsController.state.values.belt_template_collection_enabled
                            onToggled: settingsController.setValue(
                                           "belt_template_collection_enabled", checked)
                            Layout.fillWidth: true
                        }
                        SignalButton {
                            text: "Open Samples"
                            compact: true
                            onClicked: settingsController.openPath("belt_samples")
                        }
                    }
                }
            }
        }
    }
}
