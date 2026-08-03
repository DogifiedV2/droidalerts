from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.timers import _StandaloneTimerHandle


class StandaloneTimerHandleTests(unittest.TestCase):
    def make_handle(self, *, alive: bool = False):
        process = Mock()
        process.is_alive.return_value = alive
        process_stop = Mock()
        reminder_queue = Mock()
        parent_connection = Mock()
        handle = _StandaloneTimerHandle(
            process,
            None,
            process_stop,
            reminder_queue,
            parent_connection,
        )
        return handle, process, process_stop, reminder_queue, parent_connection

    def test_stop_gracefully_releases_every_child_resource(self) -> None:
        handle, process, process_stop, queue, parent = self.make_handle()

        handle.stop()
        handle.stop()

        process_stop.set.assert_called_once_with()
        parent.close.assert_called_once_with()
        process.join.assert_called_once_with(1.5)
        process.terminate.assert_not_called()
        queue.close.assert_called_once_with()
        queue.join_thread.assert_called_once_with()

    def test_stop_terminates_a_child_that_ignores_the_signal(self) -> None:
        handle, process, _process_stop, _queue, _parent = self.make_handle(alive=True)

        handle.stop(timeout=0.1)

        process.terminate.assert_called_once_with()
        self.assertEqual([call(0.1), call(1.0)], process.join.call_args_list)


if __name__ == "__main__":
    unittest.main()
