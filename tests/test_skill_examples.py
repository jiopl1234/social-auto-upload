"""Skill examples preview one command and cannot silently publish or target another platform."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sau_cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def example(platform, *args):
    script = ROOT / f'skills/{platform}-upload/scripts/examples/{platform}_cli_template.py'
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, encoding='utf-8',
                          env={**os.environ, 'PYTHONUTF8': '1'}, cwd=ROOT)


@pytest.mark.parametrize('platform', ['douyin', 'xiaohongshu', 'tencent', 'bilibili', 'kuaishou'])
def test_default_examples_only_preview_and_do_not_create_database(tmp_path, platform):
    db = tmp_path / 'never-created.sqlite3'
    args = ('capabilities', '--db', db) if platform in {'douyin', 'xiaohongshu', 'tencent'} else ('check', '--account', 'demo')
    result = example(platform, *args)
    assert result.returncode == 0
    preview = json.loads(result.stdout)
    assert preview['mode'] == 'preview' and not db.exists()
    parsed = build_parser().parse_args(preview['argv'][6:])
    assert parsed.platform == ('jobs' if platform in {'douyin', 'xiaohongshu', 'tencent'} else platform)


def test_explicit_read_only_execution_returns_actual_cli_result(tmp_path):
    result = example('tencent', 'capabilities', '--db', tmp_path / 'tasks.sqlite3', '--execute')
    assert result.returncode == 0 and len(json.loads(result.stdout)['capabilities']) == 5


def test_channel_verification_wait_and_unspecified_batch_run_are_rejected(tmp_path):
    for args in [('run', '--verification-wait', '30', '--job', 'demo', '--headed'), ('run',)]:
        result = example('tencent', *args, '--db', tmp_path / 'tasks.sqlite3')
        assert result.returncode == 2 and not (tmp_path / 'tasks.sqlite3').exists()


def test_platform_wrapper_rejects_other_platform_manifest(tmp_path):
    manifest = tmp_path / 'content.json'
    manifest.write_text('{"platform":"douyin"}', encoding='utf-8')
    result = example('tencent', 'enqueue', '--db', tmp_path / 'tasks.sqlite3', '--manifest', manifest)
    assert result.returncode == 2 and not (tmp_path / 'tasks.sqlite3').exists()


@pytest.mark.parametrize('platform,args', [('bilibili', ('check', '--headed')),
                                         ('kuaishou', ('upload-video', '--file', 'unused', '--title', 'title', '--tid', '1'))])
def test_legacy_examples_reject_unsupported_options_without_execution(platform, args):
    result = example(platform, *args, '--account', 'demo')
    assert result.returncode == 2 and not result.stdout
