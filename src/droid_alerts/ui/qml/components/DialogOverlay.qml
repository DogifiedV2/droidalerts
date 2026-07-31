import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DroidAlerts.Components 1.0

// Controllers are injected as context properties by application.py.
// qmllint disable unqualified

Item {
    id: overlay
    anchors.fill: parent
    visible: dialogController.state.visible
    z: 1000

    // Repeater.itemAt() is statically typed as Item. Each delegate below
    // declares the properties read here, but qmllint cannot infer that type.
    // qmllint disable missing-property
    function acceptDialog() {
        var kind = dialogController.state.kind
        var payload = {}
        if (kind === "form" || kind === "channel") {
            for (var i = 0; i < formRepeater.count; ++i) {
                var field = formRepeater.itemAt(i)
                if (field) payload[field["fieldId"]] = field["value"]
            }
        }
        if (kind === "choice") {
            payload.selected = ""
            for (var j = 0; j < choiceRepeater.count; ++j) {
                var choice = choiceRepeater.itemAt(j)
                if (choice && choice["checked"])
                    payload.selected = choice["optionId"]
            }
        } else if (kind === "multi-choice" || kind === "channel") {
            payload.selected = []
            for (var k = 0; k < choiceRepeater.count; ++k) {
                var multi = choiceRepeater.itemAt(k)
                if (multi && multi["checked"])
                    payload.selected.push(multi["optionId"])
            }
        } else if (kind === "rules") {
            payload.values = {}
            for (var n = 0; n < ruleRepeater.count; ++n) {
                var rule = ruleRepeater.itemAt(n)
                if (rule && rule["currentValue"].length)
                    payload.values[rule["optionId"]] = rule["currentValue"]
            }
        }
        dialogController.accept(payload)
    }
    // qmllint enable missing-property

    Rectangle {
        anchors.fill: parent
        color: "#000000"
        opacity: overlay.visible ? 0.71 : 0

        MouseArea {
            anchors.fill: parent
        }

        Behavior on opacity {
            NumberAnimation { duration: 120 }
        }
    }

    Rectangle {
        x: panel.x + 8
        y: panel.y + 13
        width: panel.width
        height: panel.height
        radius: panel.radius + 3
        color: "#58000000"
    }

    Rectangle {
        x: panel.x + 3
        y: panel.y + 6
        width: panel.width
        height: panel.height
        radius: panel.radius + 1
        color: "#36000000"
    }

    Rectangle {
        id: panel
        readonly property bool wide: ["choice", "multi-choice", "rules", "channel"]
                                     .indexOf(dialogController.state.kind) >= 0
        readonly property int preferredWidth: wide ? 700
                                                    : dialogController.state.kind === "form"
                                                      ? 560 : 460
        width: Math.min(preferredWidth, overlay.width - 48)
        height: Math.min(720, contentColumn.implicitHeight + 40)
        anchors.centerIn: parent
        radius: 14
        color: Theme.surfaceRaised
        border.color: Theme.lineRaised
        border.width: 1
        scale: overlay.visible ? 1 : 0.97

        Behavior on scale {
            NumberAnimation {
                duration: 140
                easing.type: Easing.OutCubic
            }
        }

        ColumnLayout {
            id: contentColumn
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 11

                Rectangle {
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    radius: 8
                    color: Theme.accentSoft
                    border.color: Qt.rgba(
                                      Theme.toneColor(dialogController.state.tone).r,
                                      Theme.toneColor(dialogController.state.tone).g,
                                      Theme.toneColor(dialogController.state.tone).b,
                                      0.48)

                    NavIcon {
                        anchors.centerIn: parent
                        name: dialogController.state.icon
                        tint: Theme.toneColor(dialogController.state.tone)
                        width: 16
                        height: 16
                    }
                }

                ColumnLayout {
                    spacing: 0
                    Layout.fillWidth: true

                    Text {
                        text: dialogController.state.title
                        color: Theme.ink
                        font.family: Theme.displayFont
                        font.pixelSize: 17
                        font.weight: Font.DemiBold
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
            }

            Text {
                text: dialogController.state.message
                visible: text.length > 0
                color: Theme.ink
                font.family: Theme.bodyFont
                font.pixelSize: 15
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            SignalField {
                id: ruleSearch
                visible: dialogController.state.kind === "rules"
                placeholderText: "Search droids…"
                Layout.fillWidth: true
            }

            Flickable {
                id: scroll
                visible: dialogController.state.kind === "form"
                         || dialogController.state.kind === "channel"
                         || dialogController.state.kind === "choice"
                         || dialogController.state.kind === "multi-choice"
                         || dialogController.state.kind === "rules"
                         || dialogController.state.note.length > 260
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(480, innerColumn.implicitHeight)
                Layout.minimumHeight: Math.min(340, innerColumn.implicitHeight)
                clip: true
                contentWidth: width
                contentHeight: innerColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                ColumnLayout {
                    id: innerColumn
                    width: scroll.width - 12
                    spacing: 8

                    Text {
                        visible: dialogController.state.kind === "channel"
                                 && formRepeater.count > 0
                        text: "CONNECTION"
                        color: Theme.dim
                        font.family: Theme.monoFont
                        font.pixelSize: 9
                        font.letterSpacing: 1.3
                        Layout.topMargin: 2
                    }

                    Repeater {
                        id: formRepeater
                        model: dialogController.state.kind === "form"
                               || dialogController.state.kind === "channel"
                               ? dialogController.state.fields : []

                        ColumnLayout {
                            required property var modelData
                            property string fieldId: String(modelData.id)
                            property string value: fieldInput.text
                            Layout.fillWidth: true
                            spacing: 5

                            Text {
                                text: modelData.label
                                color: Theme.muted
                                font.family: Theme.bodyFont
                                font.pixelSize: 11
                            }

                            SignalField {
                                id: fieldInput
                                text: modelData.value || ""
                                echoMode: modelData.password ? TextInput.Password : TextInput.Normal
                                Layout.fillWidth: true
                            }
                        }
                    }

                    ButtonGroup {
                        id: radioGroup
                    }

                    Rectangle {
                        visible: dialogController.state.kind === "channel"
                                 && choiceRepeater.count > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        Layout.topMargin: 5
                        Layout.bottomMargin: 3
                        color: Theme.line
                    }

                    Text {
                        visible: dialogController.state.kind === "channel"
                                 && choiceRepeater.count > 0
                        text: "ALERTS"
                        color: Theme.dim
                        font.family: Theme.monoFont
                        font.pixelSize: 9
                        font.letterSpacing: 1.3
                    }

                    Repeater {
                        id: choiceRepeater
                        model: dialogController.state.kind === "choice"
                               || dialogController.state.kind === "multi-choice"
                               || dialogController.state.kind === "channel"
                               ? dialogController.state.options : []

                        SignalCheck {
                            required property var modelData
                            property string optionId: String(modelData.id)
                            text: modelData.detail
                                  ? modelData.label + "  ·  " + modelData.detail
                                  : modelData.label
                            checked: Boolean(modelData.selected)
                            ButtonGroup.group: dialogController.state.kind === "choice"
                                               ? radioGroup : null
                            Layout.fillWidth: true
                        }
                    }

                    Repeater {
                        id: ruleRepeater
                        model: dialogController.state.kind === "rules"
                               ? dialogController.state.options : []

                        RowLayout {
                            required property var modelData
                            property string optionId: String(modelData.id)
                            property string currentValue: ruleCombo.currentValue
                            visible: !ruleSearch.text.length
                                     || String(modelData.label).toLowerCase().indexOf(
                                         ruleSearch.text.toLowerCase()) >= 0
                                     || String(modelData.detail || "").toLowerCase().indexOf(
                                         ruleSearch.text.toLowerCase()) >= 0
                            Layout.fillWidth: true
                            spacing: 10

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    text: modelData.label
                                    color: Theme.ink
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: modelData.detail || ""
                                    visible: text.length > 0
                                    color: Theme.muted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: 11
                                }
                            }

                            SignalCombo {
                                id: ruleCombo
                                model: dialogController.state.choices
                                implicitWidth: 150
                                Component.onCompleted: {
                                    for (var i = 0; i < count; ++i) {
                                        if (valueAt(i) === modelData.value) {
                                            currentIndex = i
                                            break
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        text: dialogController.state.note
                        visible: text.length > 0
                        color: Theme.muted
                        font.family: Theme.bodyFont
                        font.pixelSize: 15
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                        Layout.fillWidth: true
                    }
                }
            }

            Text {
                text: dialogController.state.note
                visible: dialogController.state.note.length > 0
                         && dialogController.state.kind !== "form"
                         && dialogController.state.kind !== "choice"
                         && dialogController.state.kind !== "multi-choice"
                         && dialogController.state.kind !== "rules"
                         && dialogController.state.kind !== "channel"
                         && dialogController.state.note.length <= 260
                color: Theme.muted
                font.family: Theme.bodyFont
                font.pixelSize: 15
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Text {
                visible: dialogController.state.linkText.length > 0
                text: "<a style='color:#39c6d8' href='" + dialogController.state.linkUrl
                      + "'>" + dialogController.state.linkText + "</a>"
                textFormat: Text.RichText
                color: Theme.accent
                font.family: Theme.bodyFont
                font.pixelSize: 12
                onLinkActivated: function(link) { Qt.openUrlExternally(link) }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Item { Layout.fillWidth: true }

                SignalButton {
                    visible: dialogController.state.cancelText.length > 0
                    text: dialogController.state.cancelText
                    tone: "ghost"
                    onClicked: dialogController.cancel()
                }

                SignalButton {
                    id: acceptButton
                    text: dialogController.state.acceptText
                    tone: dialogController.state.tone === "danger" ? "danger" : "primary"
                    defaultButton: true
                    onClicked: overlay.acceptDialog()
                }
            }
        }
    }

    Shortcut {
        sequence: "Escape"
        enabled: overlay.visible
        onActivated: dialogController.cancel()
    }

    Shortcut {
        sequences: [StandardKey.InsertParagraphSeparator,
                    StandardKey.InsertLineSeparator]
        enabled: overlay.visible
        onActivated: overlay.acceptDialog()
    }

    onVisibleChanged: {
        if (visible)
            Qt.callLater(function() { acceptButton.forceActiveFocus() })
    }
}
