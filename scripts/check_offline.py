"""Run the locked, local-only acceptance checks from the repository root."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
PER_TEST_SECONDS = 90
PYTEST_SUITE_SECONDS = 1800
PYRIGHT_SECONDS = 300


def _ensure_local_conf() -> None:
    """Initialize the example once; never replace a user's existing config."""
    target = ROOT / "conf.py"
    if target.exists():
        return
    example = ROOT / "conf.example.py"
    if not example.is_file():
        raise RuntimeError("conf.example.py is missing")
    try:
        with example.open("rb") as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination)
    except FileExistsError:
        return
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print("Initialized conf.py from conf.example.py", flush=True)


def _require_browser() -> Path:
    """Use the same installed-browser probe as the local browser tests."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests.test_browser_lifecycle import _installed_chromium_executable

    browser = _installed_chromium_executable()
    if browser is None:
        raise RuntimeError(
            "No local Chrome, Chromium, or Edge executable was found; "
            "offline acceptance cannot count browser tests as skipped"
        )
    return browser.resolve(strict=True)


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _run(label: str, command: list[str], *, env: dict[str, str], seconds: int,
         report: Path | None) -> int:
    display = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    print(f"[{label}] {display}", flush=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=flags,
        start_new_session=os.name != "nt",
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process_tree(process)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output = "Process output did not close after termination.\n"
    output = output or ""
    if timed_out:
        output += f"\n[{label}] exceeded {seconds} seconds\n"
    print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if report is not None:
        report.write_text(output, encoding="utf-8")
        print(f"[{label}] report: {report}", flush=True)
    return 124 if timed_out else (process.returncode or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path,
                        help="write offline-pytest.txt/.xml and offline-pyright.txt here")
    parser.add_argument("--collect-only", action="store_true",
                        help="check test collection without executing tests or pyright")
    parser.add_argument("--check-browser", action="store_true",
                        help="check the installed browser and exit")
    args = parser.parse_args(argv)
    try:
        _ensure_local_conf()
        browser = _require_browser()
    except (OSError, ImportError, RuntimeError) as exc:
        print(f"offline preflight failed: {exc}", file=sys.stderr)
        return 1
    print(f"Local browser: {browser}", flush=True)
    if args.check_browser:
        return 0

    report_dir = args.report_dir.resolve() if args.report_dir is not None else None
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="sau-offline-") as runtime:
        env = os.environ.copy()
        env["SAU_RUNTIME_DIR"] = runtime
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env.pop("PYTEST_ADDOPTS", None)
        pytest_command = [sys.executable, "-m", "pytest", "-p", "pytest_timeout", "-q"]
        if args.collect_only:
            pytest_command.append("--collect-only")
        else:
            pytest_command.extend((f"--timeout={PER_TEST_SECONDS}", "--timeout-method=thread"))
        if report_dir is not None:
            pytest_command.append(f"--junitxml={report_dir / 'offline-pytest.xml'}")
        tests_code = _run(
            "pytest", pytest_command, env=env, seconds=PYTEST_SUITE_SECONDS,
            report=report_dir / "offline-pytest.txt" if report_dir is not None else None,
        )
        if args.collect_only:
            return tests_code
        types_code = _run(
            "pyright", [sys.executable, "-m", "pyright", "orchestrator", "sau_cli.py"], env=env,
            seconds=PYRIGHT_SECONDS,
            report=report_dir / "offline-pyright.txt" if report_dir is not None else None,
        )
    return 0 if tests_code == 0 and types_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
