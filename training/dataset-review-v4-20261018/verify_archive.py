"""Verify the pinned review increment without extracting or executing it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def verify(path):
    info = json.loads(Path(__file__).with_name('release_info.json').read_text())
    root = 'Forte_Review_Increment_v4_20261018'
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != info['sha256'] or path.stat().st_size != info['bytes']:
        raise ValueError('Archive size or SHA256 mismatch')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != info['files'] or len(names) != len(set(names)):
            raise ValueError('Member count or uniqueness mismatch')
        for member in archive.infolist():
            p = PurePosixPath(member.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in member.filename:
                raise ValueError('Unsafe member path')
            if not member.filename.startswith(root + '/') or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Unexpected archive root or symlink')
        if archive.testzip() is not None:
            raise ValueError('Archive CRC mismatch')
        checks = {}
        for line in archive.read(root + '/CHECKSUMS.sha256').decode().splitlines():
            expected, relative = line.split('  ', 1)
            if relative in checks:
                raise ValueError('Duplicate checksum entry')
            checks[relative] = expected
        if set(names) != {root + '/' + p for p in checks} | {root + '/CHECKSUMS.sha256'}:
            raise ValueError('Incomplete checksum coverage')
        for relative, expected in checks.items():
            with archive.open(root + '/' + relative) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Member SHA256 mismatch: ' + relative)
        manifest = json.loads(archive.read(root + '/manifest.json'))
        if manifest['ready_for_training'] or manifest['human_verified_records'] != 0:
            raise ValueError('Unexpected review status')
    return {'status': 'PASS', 'sha256': digest, 'files': len(names),
            'checksums_verified': len(checks), 'ready_for_training': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive), indent=2))
