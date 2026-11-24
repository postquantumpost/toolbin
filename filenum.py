#!/usr/bin/env python3
"""Number files in filename order without overwriting existing paths."""

import argparse
import os
import sys


def nonnegative_integer(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('starting number must be an integer') from error
    if number < 0:
        raise argparse.ArgumentTypeError('starting number must be nonnegative')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='tag before the number; use "" for no tag')
    parser.add_argument('start', type=nonnegative_integer, help='starting number')
    parser.add_argument('files', nargs='*', help='files to rename')
    args = parser.parse_args(argv)

    if os.sep in args.tag or (os.altsep and os.altsep in args.tag):
        parser.error('tag must not contain a path separator')

    renames = []
    sources = set()
    targets = set()
    for number, source in enumerate(sorted(args.files, key=os.fsencode), args.start):
        source_key = os.path.abspath(source)
        if source_key in sources:
            parser.error(f'file specified more than once: {source}')
        sources.add(source_key)
        if not os.path.isfile(source):
            parser.error(f'not a file: {source}')
        stem, extension = os.path.splitext(source)
        suffix = f'_{args.tag}_{number}' if args.tag else f'_{number}'
        target = f'{stem}{suffix}{extension}'
        target_key = os.path.abspath(target)
        if target_key in targets or os.path.lexists(target):
            parser.error(f'destination already exists: {target}')
        targets.add(target_key)
        renames.append((source, target))

    try:
        for source, target in renames:
            os.rename(source, target)
    except OSError as error:
        print(f'filenum: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
