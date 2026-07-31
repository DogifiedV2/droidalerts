from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.ui.state import StateObject, UiDispatcher


class StateObjectTests(unittest.TestCase):
    def test_state_is_copied_and_emits_only_when_changed(self):
        state = StateObject({"status": "Stopped"})
        changed = Mock()
        state.stateChanged.connect(changed)

        snapshot = state.state_snapshot()
        snapshot["status"] = "mutated"
        self.assertEqual("Stopped", state.state_snapshot()["status"])

        state.update_state(status="Stopped")
        changed.assert_not_called()
        state.update_state(status="Running")
        changed.assert_called_once_with()


class UiDispatcherTests(unittest.TestCase):
    def test_callbacks_run_in_fifo_order(self):
        dispatcher = UiDispatcher()
        called: list[str] = []
        dispatcher.post(lambda: called.append("first"))
        dispatcher.post(lambda: called.append("last"))
        self.assertEqual(["first", "last"], called)

    def test_callback_failure_does_not_break_later_dispatch(self):
        dispatcher = UiDispatcher()
        called: list[str] = []
        dispatcher.post(
            lambda: (_ for _ in ()).throw(RuntimeError("bad callback"))
        )
        dispatcher.post(lambda: called.append("still works"))
        self.assertEqual(["still works"], called)

    def test_closed_dispatcher_drops_late_callbacks(self):
        dispatcher = UiDispatcher()
        called = Mock()
        dispatcher.close()
        dispatcher.post(called)
        called.assert_not_called()

    def test_worker_thread_emits_through_the_qt_bridge(self):
        dispatcher = UiDispatcher()
        called = threading.Event()
        worker = threading.Thread(target=lambda: dispatcher.post(called.set))
        worker.start()
        worker.join()
        # No event loop is running, so the queued callback must stay pending.
        self.assertFalse(called.is_set())


if __name__ == "__main__":
    unittest.main()
