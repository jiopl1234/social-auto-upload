"""Preview one real sau jobs command; --execute opts into that one operation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def main(expected_platform=None, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['capabilities', 'accounts', 'list', 'enqueue', 'run', 'status', 'attempts'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--runtime-dir')
    parser.add_argument('--media-root', action='append', default=[])
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--job')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--max-attempts', type=int, choices=range(1, 6), default=2)
    parser.add_argument('--verification-wait', type=int, default=0)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'enqueue' and not args.manifest:
        parser.error('enqueue requires --manifest')
    if args.action in {'run', 'status', 'attempts'} and not args.job:
        parser.error('this action requires --job; batch publication is not a template default')
    if args.action != 'run' and (args.headed or args.prepare_only or args.verification_wait):
        parser.error('browser and preparation options apply only to run')
    if not 0 <= args.verification_wait <= 600 or (args.verification_wait and (not args.headed or args.prepare_only)):
        parser.error('verification wait requires one headed non-preparation run and 1..600 seconds')
    if expected_platform == 'tencent' and args.verification_wait:
        parser.error('Channels does not support verification wait')
    if expected_platform and args.action == 'enqueue':
        value = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        if value.get('platform') != expected_platform:
            parser.error('manifest platform does not match this platform example')
    command = ['uv', 'run', '--frozen', '--offline', '--no-sync', 'sau', 'jobs', '--db', args.db, '--json']
    if args.runtime_dir:
        command += ['--runtime-dir', args.runtime_dir]
    for root in args.media_root:
        command += ['--media-root', root]
    prefix = list(command)
    command += [args.action]
    if args.action == 'enqueue':
        command += ['--manifest', str(args.manifest)]
    elif args.action in {'status', 'attempts'}:
        command += [args.job]
    elif args.action == 'run':
        command += ['--job', args.job, '--max-attempts', str(args.max_attempts)]
        if args.headed:
            command += ['--headed']
        if args.prepare_only:
            command += ['--prepare-only']
        if args.verification_wait:
            command += ['--verification-wait', str(args.verification_wait)]
    if args.execute:
        if expected_platform and args.action in {'run', 'status', 'attempts'}:
            result = subprocess.run(prefix + ['status', args.job], env={**os.environ, 'PYTHONUTF8': '1'},
                                    capture_output=True, encoding='utf-8')
            if result.returncode:
                print(result.stdout, end='')
                return result.returncode
            if json.loads(result.stdout)['platform'] != expected_platform:
                parser.error('job platform does not match this platform example')
        return subprocess.run(command, env={**os.environ, 'PYTHONUTF8': '1'}).returncode
    print(json.dumps({'mode': 'preview', 'argv': command}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
