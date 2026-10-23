#!/usr/bin/env python3
"""Find and optionally remove duplicate files in a directory.

Pass one handles Windows style copies named "name (n).suffix" (B) by
comparing them with the original "name.suffix" (A). Pass two compares the
cryptographic sums of all files in the directory.

Without --modify only messages are printed; with --modify the described
renames and removals are performed.
"""

import argparse
import hashlib
import os
import re
import sys

# Matches "name (n).suffix" and "name (n)" (no suffix).
DUP_NAME_RE = re.compile(r'^(?P<name>.+) \((?P<n>\d+)\)(?P<suffix>\.[^.]*)?$')

CHUNK_SIZE = 1024 * 1024


def _hash_algorithm():
    for algo in ('sha256', 'sha512', 'sha1', 'md5'):
        if algo in hashlib.algorithms_available:
            return algo
    raise RuntimeError('no supported hash algorithm available')


class Unduper:
    def __init__(self, modify=False, out=sys.stdout):
        self.modify = modify
        self.out = out
        self.hash_algo = _hash_algorithm()
        self._hash_cache = {}

    def message(self, text):
        print(text, file=self.out)

    def file_sum(self, path):
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_size, st.st_mtime_ns, st.st_ino)
        digest = self._hash_cache.get(key)
        if digest is None:
            h = hashlib.new(self.hash_algo)
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(CHUNK_SIZE), b''):
                    h.update(chunk)
            digest = h.hexdigest()
            self._hash_cache[key] = digest
        return digest

    def same_contents(self, a, b):
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        return self.file_sum(a) == self.file_sum(b)

    @staticmethod
    def list_files(dirpath):
        """Regular files (not symlinks) in dirpath, sorted by name."""
        with os.scandir(dirpath) as it:
            names = [e.name for e in it if e.is_file(follow_symlinks=False)]
        return sorted(names)

    def remove(self, path):
        if self.modify:
            os.remove(path)

    def process_dir(self, dirpath='.'):
        self.pass_one(dirpath)
        self.pass_two(dirpath)

    def pass_one(self, dirpath):
        """Handle "name (n).suffix" copies; restarts after each rename."""
        while self._pass_one_scan(dirpath):
            pass

    def _pass_one_scan(self, dirpath):
        """Return True if a rename happened and the scan must restart."""
        for b_name in self.list_files(dirpath):
            m = DUP_NAME_RE.match(b_name)
            if not m:
                continue
            b_path = os.path.join(dirpath, b_name)
            if not os.path.lexists(b_path):
                continue
            a_name = m.group('name') + (m.group('suffix') or '')
            a_path = os.path.join(dirpath, a_name)
            if not os.path.lexists(a_path):
                self.message(f'"{b_path}" is present but "{a_path}" is not')
                if self.modify:
                    os.rename(b_path, a_path)
                    return True
                continue
            if not os.path.isfile(a_path) or os.path.islink(a_path):
                continue
            if self.same_contents(a_path, b_path):
                self.message(f'"{a_path}" and "{b_path}" are the same')
                self.remove(b_path)
        return False

    def pass_two(self, dirpath):
        """Compare sums of all files; keep the first of each group."""
        by_size = {}
        for name in self.list_files(dirpath):
            path = os.path.join(dirpath, name)
            by_size.setdefault(os.path.getsize(path), []).append(path)

        for paths in by_size.values():
            if len(paths) < 2:
                continue
            by_sum = {}
            for path in paths:
                by_sum.setdefault(self.file_sum(path), []).append(path)
            for group in by_sum.values():
                # Prefer keeping a file without a "(n)" copy-style name.
                group.sort(key=lambda p: (DUP_NAME_RE.match(os.path.basename(p)) is not None, p))
                a_path = group[0]
                for b_path in group[1:]:
                    self.message(f'"{a_path}" and "{b_path}" are the same')
                    self.remove(b_path)


def process_dir(dirpath='.', modify=False):
    Unduper(modify=modify).process_dir(dirpath)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--modify', action='store_true',
                        help='rename and remove files instead of only reporting')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    process_dir('.', modify=args.modify)
    return 0


if __name__ == '__main__':
    sys.exit(main())
