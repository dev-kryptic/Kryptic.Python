"""Tests against a mock daemon: a unix-socket server speaking PROTOCOL.md v1."""

import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import kryptic


class MockDaemon:
    def __init__(self, handler):
        self.path = str(Path(tempfile.mkdtemp()) / "daemon.sock")
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(1)
        self._handler = handler
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                buffer = b""
                while b"\n" not in buffer:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                if buffer:
                    request = json.loads(buffer.split(b"\n", 1)[0])
                    connection.sendall((json.dumps(self._handler(request)) + "\n").encode())

    def close(self):
        self._server.close()


class InjectTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Path(self.temp_dir, "kryptic.json").write_text(json.dumps({"projectId": "proj_test123456"}))
        self._old_cwd = os.getcwd()
        os.chdir(self.temp_dir)

        for variable in ("KRYPTIC_DISABLED", "KRYPTIC_PROJECT_ID", "KRYPTIC_ENV",
                         "ENVIRONMENT", "INJECTED_KEY", "EXISTING_KEY"):
            os.environ.pop(variable, None)
        os.environ["KRYPTIC_SILENT"] = "true"
        self.daemon = None

    def tearDown(self):
        os.chdir(self._old_cwd)
        if self.daemon:
            self.daemon.close()
        os.environ.pop("KRYPTIC_SOCKET_PATH", None)

    def start_daemon(self, handler):
        self.daemon = MockDaemon(handler)
        os.environ["KRYPTIC_SOCKET_PATH"] = self.daemon.path

    def test_injects_secrets_into_environ(self):
        seen = {}

        def handler(request):
            seen.update(request)
            return {"v": 1, "ok": True, "secrets": [{"key": "INJECTED_KEY", "value": "from-daemon"}]}

        self.start_daemon(handler)
        result = kryptic.inject()

        self.assertFalse(result.skipped)
        self.assertEqual(result.injected, 1)
        self.assertEqual(os.environ["INJECTED_KEY"], "from-daemon")
        self.assertEqual(seen["projectId"], "proj_test123456")
        self.assertEqual(seen["environment"], "development")

    def test_never_overwrites_existing_variables(self):
        os.environ["EXISTING_KEY"] = "real-env-wins"
        self.start_daemon(lambda r: {"v": 1, "ok": True, "secrets": [{"key": "EXISTING_KEY", "value": "x"}]})

        result = kryptic.inject()

        self.assertEqual(result.injected, 0)
        self.assertEqual(os.environ["EXISTING_KEY"], "real-env-wins")

    def test_noop_when_daemon_missing(self):
        os.environ["KRYPTIC_SOCKET_PATH"] = str(Path(self.temp_dir) / "missing.sock")

        result = kryptic.inject()

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "daemon_unreachable")

    def test_noop_in_production(self):
        os.environ["ENVIRONMENT"] = "production"

        result = kryptic.inject()

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "environment_production")

    def test_noop_when_disabled(self):
        os.environ["KRYPTIC_DISABLED"] = "true"

        result = kryptic.inject()

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "disabled")

    def test_handles_error_responses(self):
        self.start_daemon(lambda r: {"v": 1, "ok": False, "error": "access_denied"})

        result = kryptic.inject()

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "access_denied")

    def test_env_overrides_win(self):
        os.environ["KRYPTIC_PROJECT_ID"] = "proj_override0001"
        os.environ["KRYPTIC_ENV"] = "staging"
        seen = {}

        def handler(request):
            seen.update(request)
            return {"v": 1, "ok": True, "secrets": []}

        self.start_daemon(handler)
        kryptic.inject()

        self.assertEqual(seen["projectId"], "proj_override0001")
        self.assertEqual(seen["environment"], "staging")


if __name__ == "__main__":
    unittest.main()
