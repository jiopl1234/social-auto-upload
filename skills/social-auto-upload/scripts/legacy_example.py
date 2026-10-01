"""Preview a single Bilibili/Kuaishou legacy operation; never promise task recovery."""
import argparse
import json
import os
import subprocess


def main(platform, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = ['login', 'check', 'upload-video'] + (['upload-note'] if platform == 'kuaishou' else [])
    parser.add_argument('action', choices=actions)
    parser.add_argument('--account', required=True)
    parser.add_argument('--file')
    parser.add_argument('--images', nargs='+')
    parser.add_argument('--title')
    parser.add_argument('--desc')
    parser.add_argument('--note')
    parser.add_argument('--tid', type=int)
    parser.add_argument('--tags')
    parser.add_argument('--schedule')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if args.action.startswith('upload-') and not args.title:
        parser.error('upload requires --title')
    if args.action == 'upload-video' and not args.file:
        parser.error('upload-video requires --file')
    if args.action == 'upload-note' and not args.images:
        parser.error('upload-note requires --images')
    if platform == 'bilibili' and args.headed:
        parser.error('Bilibili has no --headed option')
    if platform == 'bilibili' and args.action == 'upload-video' and (args.tid is None or args.desc is None):
        parser.error('Bilibili video requires --tid and --desc')
    if args.action == 'check' and args.headed:
        parser.error('check has no --headed option')
    allowed = ({'file', 'title', 'desc', 'tags', 'schedule'} | ({'tid'} if platform == 'bilibili' else set())
               if args.action == 'upload-video' else {'images', 'title', 'note', 'tags', 'schedule'}
               if args.action == 'upload-note' else set())
    for name in ('file', 'images', 'title', 'desc', 'note', 'tid', 'tags', 'schedule'):
        if getattr(args, name) is not None and name not in allowed:
            parser.error(f'--{name} does not apply to this platform/action')
    command = ['uv', 'run', '--frozen', '--offline', '--no-sync', 'sau', platform, args.action, '--account', args.account]
    if args.action.startswith('upload-'):
        for name in ('file', 'title', 'desc', 'note', 'tid', 'tags', 'schedule'):
            value = getattr(args, name)
            if value is not None:
                command += ['--' + name, str(value)]
        if args.images:
            command += ['--images', *args.images]
    if args.headed:
        command += ['--headed']
    if args.execute:
        return subprocess.run(command, env={**os.environ, 'PYTHONUTF8': '1'}).returncode
    print(json.dumps({'mode': 'preview', 'argv': command}, ensure_ascii=True))
    return 0
