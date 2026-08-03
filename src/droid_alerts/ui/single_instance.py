from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QDir, QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = "DroidAlerts.Gui.v1"
ACTIVATE_MESSAGE = b"activate"


class SingleInstanceGuard(QObject):
    """Own the GUI's local endpoint and redirect later launches to it."""

    activationRequested = Signal()

    def __init__(self, name: str = SERVER_NAME) -> None:
        super().__init__()
        self.name = name
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)
        lock_name = self.name.replace("/", "_").replace("\\", "_") + ".lock"
        temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
        lock_path = QDir(temp_dir).filePath(lock_name)
        self.lock = QLockFile(lock_path)
        self._connections: set[QLocalSocket] = set()
        self._acquired = False

    def acquire(self, timeout_ms: int = 300) -> bool:
        if self._acquired:
            return True
        if not self.lock.tryLock(0) and self._notify_primary(timeout_ms):
            return False
        if not self.lock.isLocked():
            self.lock.removeStaleLockFile()
            if not self.lock.tryLock(0):
                return False

        # Owning the lock proves there is no live primary, so any endpoint is
        # stale and may be removed before listening.
        QLocalServer.removeServer(self.name)
        self._acquired = self.server.listen(self.name)
        if not self._acquired:
            self.lock.unlock()
        return self._acquired

    def _notify_primary(self, timeout_ms: int) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(max(1, int(timeout_ms))):
            return False
        socket.write(ACTIVATE_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(max(1, int(timeout_ms)))
        socket.disconnectFromServer()
        return True

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._connections.add(socket)
            socket.readyRead.connect(lambda target=socket: self._read(target))
            socket.disconnected.connect(lambda target=socket: self._discard(target))
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if bytes(socket.readAll()).strip() == ACTIVATE_MESSAGE:
            self.activationRequested.emit()

    def _discard(self, socket: QLocalSocket) -> None:
        self._connections.discard(socket)
        socket.deleteLater()

    def connect_window_activation(self, callback: Callable[[], None]) -> None:
        self.activationRequested.connect(callback)

    def close(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        for socket in tuple(self._connections):
            socket.abort()
        self._connections.clear()
        self.server.close()
        QLocalServer.removeServer(self.name)
        self.lock.unlock()


__all__ = ["SERVER_NAME", "SingleInstanceGuard"]
