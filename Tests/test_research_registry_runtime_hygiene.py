from __future__ import annotations

import subprocess
import sys


def test_runtime_package_init_does_not_preload_readiness() -> None:
    code = (
        "import sys; "
        "import research_registry.runtime; "
        "print('research_registry.runtime.readiness' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_runtime_readiness_module_executes_without_runtime_warning() -> None:
    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "research_registry.runtime.readiness"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stderr == ""
