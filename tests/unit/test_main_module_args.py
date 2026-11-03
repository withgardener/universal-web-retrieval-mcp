"""`python -m universal_web_retrieval` must honour CLI flags like the `uwr` console script."""
import os
import subprocess
import sys
from pathlib import Path

from universal_web_retrieval import __version__


def test_python_dash_m_package_prints_version():
    src = str(Path(__file__).resolve().parents[2] / "src")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")]))}
    out = subprocess.run([sys.executable, "-m", "universal_web_retrieval", "--version"],
                         capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"uwr {__version__}"
