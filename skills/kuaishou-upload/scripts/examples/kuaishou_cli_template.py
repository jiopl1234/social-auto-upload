"""Preview one legacy operation; no task idempotency/recovery guarantee."""
from pathlib import Path
import runpy

helper = Path(__file__).resolve().parents[3] / 'social-auto-upload/scripts/legacy_example.py'

if __name__ == '__main__':
    raise SystemExit(runpy.run_path(str(helper))['main']('kuaishou'))
