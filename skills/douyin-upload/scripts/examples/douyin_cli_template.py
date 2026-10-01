"""Single-command preview; pass --execute to perform the chosen CLI action."""
from pathlib import Path
import runpy

helper = Path(__file__).resolve().parents[3] / 'social-auto-upload/scripts/task_example.py'

if __name__ == '__main__':
    raise SystemExit(runpy.run_path(str(helper))['main']('douyin'))
