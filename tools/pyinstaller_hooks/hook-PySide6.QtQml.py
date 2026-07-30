"""Collect only the QML modules used by Droid Alerts."""

from pathlib import PurePosixPath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


def _used_qml_destination(destination: str) -> bool:
    marker = ("PySide6", "Qt", "qml")
    parts = PurePosixPath(destination.replace("\\", "/")).parts
    try:
        start = next(
            index
            for index in range(len(parts) - len(marker) + 1)
            if parts[index : index + len(marker)] == marker
        )
    except StopIteration:
        return False
    relative = parts[start + len(marker) :]
    if not relative:
        return False
    if relative[0] in {"QtCore", "QtNetwork"}:
        return len(relative) == 1
    if relative[0] == "QtQml":
        return len(relative) == 1 or relative[1] in {"Models", "WorkerScript"}
    if relative[0] != "QtQuick":
        return False
    if len(relative) == 1:
        return True
    if relative[1] in {"Layouts", "Shapes", "Templates", "Window"}:
        return len(relative) == 2
    if relative[1] != "Controls":
        return False
    if len(relative) == 2:
        return True
    return relative[2] in {"Basic", "impl"}


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()
binaries += [
    item for item in qml_binaries if _used_qml_destination(item[1])
]
datas += [item for item in qml_datas if _used_qml_destination(item[1])]
