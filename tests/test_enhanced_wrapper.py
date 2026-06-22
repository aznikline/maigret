import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "maigret-search"


def _fake_python(tmp_path: Path) -> tuple[Path, Path]:
    log_path = tmp_path / "commands.jsonl"
    executable = tmp_path / "python"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

if len(sys.argv) > 1 and sys.argv[1] == "-c":
    os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *sys.argv[1:]])

with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")

if sys.argv[1:3] == ["-m", "maigret"]:
    raise SystemExit(int(os.environ.get("FAKE_CORE_EXIT", "0")))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log_path


def _run_wrapper(
    tmp_path: Path,
    *args: str,
    core_exit: int = 0,
    extra_env: dict[str, str] | None = None,
):
    fake_python, log_path = _fake_python(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "MAIGRET_PYTHON": str(fake_python),
            "MAIGRET_SKIP_FLARESOLVERR": "1",
            "REAL_PYTHON": sys.executable,
            "COMMAND_LOG": str(log_path),
            "FAKE_CORE_EXIT": str(core_exit),
        }
    )
    env.update(extra_env or {})
    result = subprocess.run(
        [str(WRAPPER), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    commands = []
    if log_path.exists():
        commands = [json.loads(line) for line in log_path.read_text().splitlines()]
    return result, commands


def test_wrapper_preserves_option_values_and_uses_explicit_enhanced_defaults(tmp_path):
    result, commands = _run_wrapper(
        tmp_path,
        "alice",
        "bob",
        "--timeout",
        "10",
        "--site",
        "GitHub",
        "--proxy",
        "socks5://localhost:1080",
    )

    assert result.returncode == 0, result.stderr
    core = commands[0]
    assert core[:2] == ["-m", "maigret"]
    assert core[-8:-6] == ["alice", "bob"]
    pairs = list(zip(core, core[1:]))
    assert ("--timeout", "10") in pairs
    assert ("--site", "GitHub") in pairs
    assert ("--proxy", "socks5://localhost:1080") in pairs
    for flag in ("--txt", "--csv", "--html", "--pdf", "--md", "--graph"):
        assert flag in core
    assert core[core.index("--top-sites") + 1] == "3000"
    assert core[core.index("--json") + 1] == "simple"
    assert core[core.index("--reports-sorting") + 1] == "data"

    deep_search_targets = [cmd[-1] for cmd in commands if cmd and cmd[0].endswith("deep_search.py")]
    assert deep_search_targets == ["alice", "bob"]


def test_wrapper_stops_when_core_search_fails(tmp_path):
    result, commands = _run_wrapper(tmp_path, "alice", core_exit=7)

    assert result.returncode == 7
    assert len(commands) == 1
    assert commands[0][:2] == ["-m", "maigret"]
    assert commands[0][-1] == "alice"
    assert "post-processing skipped" in result.stderr


def test_wrapper_runs_default_phone_correlation_without_key(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "report_alice_simple.json").write_text("{}", encoding="utf-8")

    result, commands = _run_wrapper(
        tmp_path,
        "alice",
        extra_env={"MAIGRET_RESULTS_DIR": str(results_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert any(cmd and cmd[0].endswith("entity_enrich.py") for cmd in commands)


def test_wrapper_passes_explicit_report_with_optional_phone_key(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    report = results_dir / "report_alice_simple.json"
    report.write_text("{}", encoding="utf-8")

    result, commands = _run_wrapper(
        tmp_path,
        "alice",
        extra_env={
            "MAIGRET_RESULTS_DIR": str(results_dir),
            "MAIGRET_PHONE_HASH_KEY": "operator-controlled-test-key",
        },
    )

    assert result.returncode == 0, result.stderr
    enrich = next(cmd for cmd in commands if cmd and cmd[0].endswith("entity_enrich.py"))
    assert enrich[enrich.index("--report") + 1] == str(report)
    assert enrich[enrich.index("--user-name") + 1] == "alice"
    assert "--allow-phone-correlation" not in enrich


def test_wrapper_can_explicitly_disable_phone_correlation(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "report_alice_simple.json").write_text("{}", encoding="utf-8")

    result, commands = _run_wrapper(
        tmp_path,
        "alice",
        extra_env={
            "MAIGRET_RESULTS_DIR": str(results_dir),
            "MAIGRET_DISABLE_PHONE_CORRELATION": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    enrich = next(cmd for cmd in commands if cmd and cmd[0].endswith("entity_enrich.py"))
    assert "--no-phone-correlation" in enrich
