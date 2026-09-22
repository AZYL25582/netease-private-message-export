"""Atomic local writes. Backups contain private data; outputs stay outside the skill."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def output_path(path):
    p = Path(path).absolute()
    skill = Path(__file__).resolve().parents[1]
    resolved = p.resolve()
    if resolved == skill or skill in resolved.parents:
        raise ValueError('Runtime output must be outside the skill folder')
    if p.is_symlink():
        raise ValueError('Refusing a symlink output')
    return p


def atomic_write(path, data, backup=True):
    p = output_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode('utf-8')
    fd, name = tempfile.mkstemp(prefix='.' + p.name + '.', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if backup and p.exists():
            bp = p.with_name(p.name + '.backup-' + uuid.uuid4().hex)
            with open(p, 'rb') as src, open(bp, 'xb') as dst:
                os.chmod(bp, 0o600)
                shutil.copyfileobj(src, dst)
        os.replace(name, p)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
