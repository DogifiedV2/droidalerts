import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0
import "pages"

// Controllers are injected as context properties by application.py.
// qmllint disable unqualified

ApplicationWindow {
    id: window
    width: 1220
    height: 820
    minimumWidth: 980
    minimumHeight: 650
    visible: true
    title: "Droid Alerts"
    color: Theme.bg0

    onClosing: function(close) {
        appController.close()
        close.accepted = true
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: sidebar
            Layout.preferredWidth: 232
            Layout.fillHeight: true
            color: Theme.bg1

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.line
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.topMargin: 18
                anchors.bottomMargin: 16
                spacing: 0

                RowLayout {
                    Layout.leftMargin: 18
                    Layout.rightMargin: 18
                    Layout.bottomMargin: 20
                    spacing: 11

                    Rectangle {
                        Layout.preferredWidth: 38
                        Layout.preferredHeight: 38
                        radius: 9
                        color: Theme.accentSoft
                        border.color: Theme.accent
                        clip: true

                        Image {
                            anchors.fill: parent
                            anchors.margins: 4
                            source: appIconUrl
                            fillMode: Image.PreserveAspectFit
                        }
                    }

                    Column {
                        spacing: 1
                        Text {
                            text: "Droid Alerts"
                            color: Theme.ink
                            font.family: Theme.displayFont
                            font.pixelSize: 17
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: "v" + appController.state.version
                            color: Theme.muted
                            font.family: Theme.monoFont
                            font.pixelSize: 10
                        }
                    }
                }

                Repeater {
                    model: appController.state.pages

                    FocusScope {
                        id: navItem
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 43
                        activeFocusOnTab: true

                        property bool active: appController.state.page === modelData.id
                        Keys.onReturnPressed: appController.selectPage(modelData.id)
                        Keys.onEnterPressed: appController.selectPage(modelData.id)
                        Keys.onSpacePressed: appController.selectPage(modelData.id)

                        Rectangle {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            radius: 7
                            color: parent.active ? Theme.accentSoft
                                                 : navMouse.containsMouse ? Theme.bgHover
                                                                         : "transparent"
                        }

                        Rectangle {
                            visible: navItem.activeFocus
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            radius: 7
                            color: "transparent"
                            border.width: 2
                            border.color: Theme.accent
                        }

                        Rectangle {
                            visible: parent.active
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 3
                            height: 25
                            radius: 2
                            color: Theme.accent
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 20
                            anchors.rightMargin: 16
                            spacing: 11

                            NavIcon {
                                name: modelData.id
                                tint: navItem.active ? Theme.accent : Theme.muted
                                Layout.preferredWidth: 18
                                Layout.preferredHeight: 18
                            }

                            Text {
                                text: modelData.label
                                color: navItem.active ? Theme.ink : Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 13
                                font.weight: navItem.active ? Font.DemiBold : Font.Normal
                                Layout.fillWidth: true
                            }

                            Text {
                                text: modelData.number
                                color: Theme.dim
                                font.family: Theme.monoFont
                                font.pixelSize: 10
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                opacity: navItem.active || navItem.activeFocus
                                         || navMouse.containsMouse ? 1 : 0
                                Layout.preferredWidth: 21

                                Behavior on opacity {
                                    NumberAnimation { duration: 110 }
                                }
                            }
                        }

                        MouseArea {
                            id: navMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                navItem.forceActiveFocus()
                                appController.selectPage(modelData.id)
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                ColumnLayout {
                    Layout.leftMargin: 12
                    Layout.rightMargin: 12
                    Layout.bottomMargin: 12
                    spacing: 8

                    Rectangle {
                        visible: dealsController.state.available
                        Layout.fillWidth: true
                        Layout.preferredHeight: 56
                        radius: 9
                        color: Theme.bg2
                        border.color: Theme.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 9
                            spacing: 8

                            Rectangle {
                                Layout.preferredWidth: 40
                                Layout.preferredHeight: 40
                                radius: 7
                                color: Theme.bg3
                                border.color: Theme.line
                                clip: true

                                Image {
                                    anchors.fill: parent
                                    anchors.margins: 2
                                    source: dealsController.state.portrait
                                    fillMode: Image.PreserveAspectFit
                                }

                                Text {
                                    visible: dealsController.state.portrait.length === 0
                                    anchors.centerIn: parent
                                    text: "◉"
                                    color: Theme.accent
                                    font.pixelSize: 20
                                }
                            }

                            Text {
                                text: dealsController.state.sidebarLabel
                                color: Theme.ink
                                font.family: Theme.bodyFont
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                wrapMode: Text.WordWrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.leftMargin: 2
                        Layout.rightMargin: 2
                        Layout.preferredHeight: 1
                        color: Theme.line
                    }

                    Text {
                        text: "COMMUNITY"
                        color: Theme.dim
                        font.family: Theme.monoFont
                        font.pixelSize: 9
                        font.letterSpacing: 1.4
                    }

                    GridLayout {
                        columns: 2
                        columnSpacing: 5
                        rowSpacing: 5
                        Layout.fillWidth: true

                        Repeater {
                            model: [
                                {
                                    id: "discord",
                                    label: "Discord",
                                    icon: "discord",
                                    host: "discord.gg"
                                },
                                {
                                    id: "tracker",
                                    label: "Tracker",
                                    icon: "bars",
                                    host: "gonk.tools"
                                },
                                {
                                    id: "wiki",
                                    label: "Wiki",
                                    icon: "book",
                                    host: "gonk.tools"
                                },
                                {
                                    id: "stats",
                                    label: "Stats",
                                    icon: "trend",
                                    host: "gonk.tools"
                                }
                            ]

                            LinkChip {
                                required property var modelData
                                label: modelData.label
                                iconName: modelData.icon
                                destination: modelData.host
                                Layout.fillWidth: true
                                onActivated: appController.openLink(modelData.id)
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    Layout.rightMargin: 14
                    implicitHeight: 52
                    radius: 9
                    color: Theme.bg2
                    border.color: Theme.line

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 9

                        StatusLamp {
                            tone: appController.state.statusTone
                        }

                        Column {
                            spacing: 1
                            Text {
                                text: appController.state.status
                                color: Theme.ink
                                font.family: Theme.bodyFont
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: appController.state.watching ? "Chat watcher active" : "Ready"
                                color: Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 10
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.bg0

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66
                    color: Theme.bg1

                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 1
                        color: Theme.line
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 20
                        anchors.rightMargin: 20
                        spacing: 12

                        Text {
                            text: appController.state.pageTitle
                            color: Theme.ink
                            font.family: Theme.displayFont
                            font.pixelSize: 22
                            font.weight: Font.DemiBold
                        }

                        Rectangle {
                            implicitWidth: statusRow.implicitWidth + 18
                            implicitHeight: 28
                            radius: 14
                            color: Theme.bg2
                            border.color: Theme.line

                            RowLayout {
                                id: statusRow
                                anchors.centerIn: parent
                                spacing: 7
                                StatusLamp {
                                    tone: appController.state.statusTone
                                }
                                Text {
                                    text: appController.state.status
                                    color: Theme.muted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 11
                                    font.weight: Font.Medium
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }

                        SignalButton {
                            text: appController.state.watchButton
                            tone: appController.state.watching ? "danger" : "primary"
                            onClicked: appController.toggleWatching()
                        }
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: {
                        var page = appController.state.page
                        if (page === "belt") return 1
                        if (page === "deals") return 2
                        if (page === "history") return 3
                        if (page === "diagnostics") return 4
                        if (page === "settings") return 5
                        return 0
                    }

                    DashboardPage {}
                    BeltPage {}
                    DealsPage {}
                    HistoryPage {}
                    DiagnosticsPage {}
                    SettingsPage {}
                }
            }
        }
    }

    Rectangle {
        id: toast
        visible: appController.state.toastVisible
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 18
        anchors.bottomMargin: 18
        width: Math.min(420, Math.max(160, toastText.implicitWidth + 60))
        height: 44
        radius: 9
        color: Theme.bg2
        border.color: Theme.accent
        z: 900
        opacity: visible ? 1 : 0

        Behavior on opacity {
            NumberAnimation { duration: 130 }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 13
            anchors.rightMargin: 13
            spacing: 9
            StatusLamp { tone: "accent" }
            Text {
                id: toastText
                text: appController.state.toastText
                color: Theme.ink
                font.family: Theme.bodyFont
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }
    }

    DialogOverlay {}

    Shortcut { sequence: "1"; onActivated: appController.selectPageNumber(1) }
    Shortcut { sequence: "2"; onActivated: appController.selectPageNumber(2) }
    Shortcut { sequence: "3"; onActivated: appController.selectPageNumber(3) }
    Shortcut { sequence: "4"; onActivated: appController.selectPageNumber(4) }
    Shortcut { sequence: "5"; onActivated: appController.selectPageNumber(5) }
    Shortcut { sequence: "6"; onActivated: appController.selectPageNumber(6) }

    Shortcut {
        sequence: "Ctrl+T"
        onActivated: dashboardController.testAllAlerts()
    }
}
