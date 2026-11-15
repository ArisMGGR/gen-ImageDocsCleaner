import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_cleaner import tools  # noqa: E402


def run(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    return result.stdout


def exif(*args):
    return run(tools.EXIFTOOL, *args)


def ffmpeg(*args):
    return run(tools.FFMPEG, "-nostdin", "-loglevel", "error", "-y", *args)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path / "out")
