"""Content-free timing validation and task-owned live evaluation power guard."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import subprocess
import sys
import time
from typing import Any

MAX_CLOCK_SKEW_SECONDS = 5.0
ASSERTION_STARTUP_SECONDS = 0.1
ASSERTION_CLEANUP_SECONDS = 1.0


def environment_policy(live: bool) -> dict[str, Any]:
    """Describe the frozen contract without inspecting host or user state."""
    return {
        "version": 1,
        "live": live,
        "macos_idle_sleep_assertion": "task_scoped_caffeinate_i"
        if live
        else "not_required",
        "display_sleep_prevented": False,
        "persistent_power_changes": False,
        "clamshell_or_manual_sleep_prevented": False,
        "clock_checks": ["batch", "provider_attempt"] if live else [],
        "max_absolute_clock_skew_seconds": MAX_CLOCK_SKEW_SECONDS,
        "environment_failure_changes_machine_results": False,
        "automatic_retry": False,
    }


@dataclass(frozen=True)
class ClockSample:
    wall: float
    monotonic: float

    @classmethod
    def start(cls) -> ClockSample:
        return cls(time.time(), time.monotonic())

    def finish(self) -> dict[str, Any]:
        wall, active = time.time() - self.wall, time.monotonic() - self.monotonic
        skew = wall - active
        valid = (
            all(math.isfinite(n) for n in (wall, active, skew))
            and wall >= 0
            and active >= 0
            and abs(skew) <= MAX_CLOCK_SKEW_SECONDS
        )
        return {
            "wall_elapsed_seconds": round(wall, 6) if math.isfinite(wall) else None,
            "monotonic_elapsed_seconds": round(active, 6)
            if math.isfinite(active)
            else None,
            "clock_skew_seconds": round(skew, 6) if math.isfinite(skew) else None,
            "valid": valid,
            "code": "clock_continuous" if valid else "clock_discontinuity",
        }


def timing_observation(value: Any) -> dict[str, Any]:
    """Read only finite numeric timing fields; never copy arbitrary trace text."""
    if not isinstance(value, dict):
        raise ValueError("evaluation_provider_timing_invalid")
    keys = ("wall_elapsed_seconds", "monotonic_elapsed_seconds", "clock_skew_seconds")
    timing = {
        key: value.get(key)
        if type(value.get(key)) in {int, float} and math.isfinite(value[key])
        else None
        for key in keys
    }
    wall, active, skew = (timing[key] for key in keys)
    timing["valid"] = (
        value.get("valid") is True
        and None not in (wall, active, skew)
        and wall >= 0
        and active >= 0
        and abs(skew) <= MAX_CLOCK_SKEW_SECONDS
        and abs((wall - active) - skew) < 0.0001
    )
    return timing


class ExecutionEnvironment:
    """Hold only this process's macOS idle assertion; always release its child."""

    def __init__(self, live: bool) -> None:
        self.live = live
        self.process: Any = None
        self.clock: ClockSample | None = None
        self.timing: dict[str, Any] | None = None
        self.errors: list[str] = []
        self.assertion = "not_required" if not live else "not_applicable_non_macos"
        self.started = False
        self.finished = False

    def __enter__(self) -> ExecutionEnvironment:
        self.started = True
        if not self.live:
            return self
        self.clock = ClockSample.start()
        if sys.platform == "darwin":
            self._start_assertion()
        return self

    def _start_assertion(self) -> None:
        self.assertion = "setup_failed"
        try:
            self.process = subprocess.Popen(
                ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={},
            )
            try:
                self.process.wait(timeout=ASSERTION_STARTUP_SECONDS)
            except subprocess.TimeoutExpired:
                self.assertion = "active"
                return
            raise RuntimeError("evaluation_idle_assertion_setup_failed")
        except BaseException:
            self.errors.append("idle_assertion_setup_failed")
            self._release()
            self._finish_clock()
            raise RuntimeError("evaluation_idle_assertion_setup_failed") from None

    def _release(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.poll() is not None:
                if self.assertion == "active":
                    self.errors.append("idle_assertion_exited_early")
                    self.assertion = "exited_early"
                return
            self.process.terminate()
            try:
                self.process.wait(timeout=ASSERTION_CLEANUP_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=ASSERTION_CLEANUP_SECONDS)
            if self.assertion == "active":
                self.assertion = "released"
        except Exception:
            self.errors.append("idle_assertion_cleanup_failed")
            self.assertion = "cleanup_failed"

    def _finish_clock(self) -> None:
        if self.clock is not None and self.timing is None:
            self.timing = self.clock.finish()
        self.finished = True

    def __exit__(self, *_: Any) -> None:
        try:
            self._release()
        finally:
            self._finish_clock()

    def assessment(self, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        failures = list(self.errors)
        if self.live:
            if not self.finished or self.timing is None:
                failures.append("environment_measurement_incomplete")
            elif not self.timing["valid"]:
                failures.append("batch_clock_discontinuity")
            if any(not item["valid"] for item in attempts):
                failures.append("provider_clock_discontinuity")
        return {
            "schema_version": 1,
            "policy": environment_policy(self.live),
            "environment_ok": not failures,
            "status": "failed"
            if failures
            else ("valid" if self.live else "not_required"),
            "idle_assertion": self.assertion,
            "batch_timing": self.timing,
            "provider_timings": attempts,
            "error_codes": sorted(set(failures)),
        }
