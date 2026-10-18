"""Local-only CLI regression checks; no device commands or iCloud paths."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


class ICloudProbeCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="riji-probe-cli-tests-")
        cls.base = Path(cls.temporary.name).resolve()
        cls.binary = cls.base / "icloud-probe"
        ios = Path(__file__).resolve().parents[1]
        subprocess.run([
            "swiftc", str(ios / "ProbeSupport/ProbeFixture.swift"),
            str(ios / "ProbeSupport/ProbeOperations.swift"),
            str(ios / "scripts/icloud-probe/main.swift"), "-o", str(cls.binary),
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_cli(self, *arguments):
        started = time.monotonic()
        result = subprocess.run([str(self.binary), *map(str, arguments)],
                                capture_output=True, text=True, timeout=5)
        return result.returncode, json.loads(result.stdout), time.monotonic() - started

    def test_timeout_preserves_last_safe_cause(self):
        run = str(uuid.uuid4()).upper()
        root = self.base / ("Riji-iCloud-Probe-" + run)
        root.mkdir()
        code, output, elapsed = self.run_cli("poll", root, run, "pristine", "1")
        self.assertEqual(code, 124)
        self.assertEqual(output, {"status": "failed", "code": "deadline", "lastCode": "notICloud"})
        self.assertLess(elapsed, 2.5)

    def test_wrong_fixture_and_symlink_fail_without_waiting_for_budget(self):
        run = str(uuid.uuid4()).upper()
        outside = self.base / ("outside-" + run)
        outside.mkdir()
        root = self.base / ("Riji-iCloud-Probe-" + run)
        root.symlink_to(outside, target_is_directory=True)
        for path, expected in [(outside, "wrongFixture"), (root, "unsafePath")]:
            code, output, elapsed = self.run_cli("poll", path, run, "pristine", "3")
            self.assertEqual(code, 1)
            self.assertEqual(output, {"status": "failed", "code": expected})
            self.assertLess(elapsed, 2)

    def test_watchdog_ends_a_blocked_read_only_command(self):
        code, output, elapsed = self.run_cli("self-test-blocked-read")
        self.assertEqual(code, 124)
        self.assertEqual(output, {"status": "failed", "code": "deadline", "lastCode": "noCompletedAttempt"})
        self.assertLess(elapsed, 2.5)

    def test_invalid_run_is_an_error_and_local_mutation_protocol_still_passes(self):
        code, output, _ = self.run_cli("poll", self.base, "invalid", "pristine", "1")
        self.assertEqual(code, 1)
        self.assertEqual(output["code"], "invalidArguments")
        code, output, _ = self.run_cli("self-test")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "local-protocol-self-test-passed")


if __name__ == "__main__":
    unittest.main()
