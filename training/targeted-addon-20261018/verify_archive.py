"""Verify the supplementary archive and its target-only YOLO exports offline."""
import argparse
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def verify(path, expected_sha256):
    root = 'Forte_Targeted_Addon_20261018'
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected_sha256:
        raise ValueError('Archive SHA256 mismatch')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        for info in archive.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or not info.filename.startswith(root + '/'):
                raise ValueError('Unsafe member path')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unexpected symlink')
        if archive.testzip() is not None:
            raise ValueError('Archive CRC mismatch')
        sums = {}
        for line in archive.read(root + '/CHECKSUMS.sha256').decode().splitlines():
            expected, relative = line.split('  ', 1)
            if relative in sums:
                raise ValueError('Duplicate checksum entry')
            sums[relative] = expected
        if set(names) != {root + '/' + p for p in sums} | {root + '/CHECKSUMS.sha256'}:
            raise ValueError('Incomplete checksum coverage')
        for relative, expected in sums.items():
            with archive.open(root + '/' + relative) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('Member SHA256 mismatch: ' + relative)
        tasks = json.loads(archive.read(root + '/annotations/tasks.json'))
        manifest = json.loads(archive.read(root + '/manifest.json'))
        if len(tasks) != 59 or len({t['id'] for t in tasks}) != 59:
            raise ValueError('Wrong task count or repeated IDs')
        counts = collections.defaultdict(collections.Counter)
        groups = collections.defaultdict(set)
        labelled = 0
        for task in tasks:
            if task['boxes_reviewed'] or task['boxes'] or task['bank_violation_label'] is not None:
                raise ValueError('Target-only task incorrectly claims full bank truth')
            if not task['license'] or not task['source_url'] or not task['attribution']:
                raise ValueError('Missing source attribution')
            if sums[task['image_path']] != task['sha256']:
                raise ValueError('Task points to different image contents')
            split = task['auxiliary_split']
            groups[task['source_group']].add(split)
            for target, review in task.get('scoped_object_reviews', {}).items():
                labelled += 1
                if review['source_image_sha256'] != task['sha256'] or review['review_level'] != 'provisional_seed':
                    raise ValueError('Changed or misrepresented provisional review')
                if review['review_evidence'] not in sums:
                    raise ValueError('Missing visual review evidence')
                counts[target][split] += 1
                base = f'yolo/{target}'
                image_path = f"{base}/images/{split}/{task['id']}" + Path(task['image_path']).suffix
                if sums[image_path] != task['sha256']:
                    raise ValueError('YOLO image differs from reviewed source')
                label_path = f"{base}/labels/{split}/{task['id']}.txt"
                lines = archive.read(root + '/' + label_path).decode().splitlines()
                if len(lines) != len(review['boxes']):
                    raise ValueError('YOLO object count mismatch')
                for line, box in zip(lines, review['boxes']):
                    x1, y1, x2, y2 = box['xyxy_norm']
                    expected = [0, (x1+x2)/2, (y1+y2)/2, x2-x1, y2-y1]
                    actual = [float(v) for v in line.split()]
                    if len(actual) != 5 or any(abs(a-b) > 1e-7 for a, b in zip(actual, expected)):
                        raise ValueError('YOLO transform mismatch')
        if labelled != 13 or any(len(v) != 1 for v in groups.values()):
            raise ValueError('Label count or group separation mismatch')
        actual_counts = {c: {s: counts[c][s] for s in ('train', 'val', 'test')} for c in counts}
        if actual_counts != manifest['yolo_target_counts']:
            raise ValueError('Manifest counts mismatch')
    return {'status': 'PASS', 'sha256': digest, 'files': len(names),
            'new_images': len(tasks), 'provisional_target_box_images': labelled,
            'source_groups_crossing_splits': 0, 'training_executed': False,
            'annotation_accuracy_measured': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive, args.sha256), indent=2))
