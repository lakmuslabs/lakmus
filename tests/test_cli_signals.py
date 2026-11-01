"""What Ctrl-C does to a run, proved with a real signal to a real process.

These are the only tests in the suite that spawn a subprocess, and they are worth it: the
behaviour they cover — stop taking work, finish the calls in flight, write all three files, exit 3
— is the one the whole milestone is judged on, and it cannot be observed from inside the process
that would be handling the signal.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness import single_suite
from lakmus.errors import ExitCode

DRIVER = Path(__file__).parent / "signal_driver.py"
CASES = [
    {"id": f"c-{index:02d}", "question": f"q {index} ?", "expected": "oui"} for index in range(12)
]


def _start(suite: Path, out: Path, *, delay: str) -> subprocess.Popen[str]:
    environment = {**os.environ, "LAKMUS_TEST_DELAY": delay, "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        [
            sys.executable,
            str(DRIVER),
            "run",
            str(suite),
            "--target",
            "slow:local",
            "--no-code",
            "--no-cache",
            "--quiet",
            "--concurrency",
            "2",
            "--out",
            str(out),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


def _wait_for_calls(process: subprocess.Popen[str], how_many: int) -> None:
    """Block until the run is demonstrably in flight."""
    assert process.stdout is not None
    seen = 0
    while seen < how_many:
        line = process.stdout.readline()
        assert line, "the driver exited before making a call"
        if line.startswith("CALL "):
            seen += 1


@pytest.fixture
def suite(valid_suite: Path) -> Path:
    return single_suite(valid_suite, cases=CASES)


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_one_signal_stops_the_run_and_still_writes_every_file(
    suite: Path, tmp_path: Path, number: signal.Signals
) -> None:
    out = tmp_path / "out"
    process = _start(suite, out, delay="0.3")
    _wait_for_calls(process, 2)

    process.send_signal(number)
    process.communicate(timeout=60)

    assert process.returncode == ExitCode.RUNTIME, "a partial run has no verdict"
    assert sorted(entry.name for entry in out.iterdir()) == [
        "cases.jsonl.gz",
        "report.html",
        "run.json",
    ]
    written = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert written["status"] == "interrupted"
    assert 0 < written["summary"]["accuracy"]["n_cases"] < len(CASES), (
        "the calls in flight finished and the rest were never taken"
    )


def test_a_second_signal_abandons_the_run(suite: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    process = _start(suite, out, delay="2.0")
    _wait_for_calls(process, 2)

    process.send_signal(signal.SIGINT)
    # A standard signal is not queued: two sent in the same microsecond are delivered once, so
    # back-to-back sends tested nothing. A human pressing it twice leaves far more than this.
    time.sleep(0.4)
    process.send_signal(signal.SIGINT)
    _, errors = process.communicate(timeout=60)

    assert process.returncode == ExitCode.RUNTIME
    assert "abandoned" in errors
    assert not (out / "run.json").exists(), "someone pressing it twice is asking to be let go"
