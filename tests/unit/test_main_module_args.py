"""`python -m universal_web_retrieval` must honour CLI flags like the `uwr` console script."""
import subprocess
import sys

from universal_web_retrieval import __version__


def test_python_dash_m_package_prints_version():
    out = subprocess.run([sys.executable, "-m", "universal_web_retrieval", "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"uwr {__version__}"
