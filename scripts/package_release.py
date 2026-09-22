"""Build a release from an explicit allowlist after privacy checks."""
import argparse
from pathlib import Path
import sys
import zipfile
from io import BytesIO
from check_privacy import RELEASE_FILES, scan
from local_io import atomic_write


def build(root, target):
    root = Path(root).resolve()
    hits,_ = scan(root)
    if hits: raise ValueError('Privacy/release check failed; run check_privacy.py for locations')
    buf = BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(RELEASE_FILES):
            z.write(root/name, 'netease-chat-export/'+name)
    with zipfile.ZipFile(BytesIO(buf.getvalue())) as z:
        if z.testzip() is not None or len(z.namelist())!=len(RELEASE_FILES):
            raise ValueError('ZIP validation failed')
    atomic_write(target,buf.getvalue())


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output'); a=p.parse_args()
    try:
        build(Path(__file__).resolve().parents[1],a.output)
        print('Release written from allowlist only')
    except (ValueError,OSError):
        print('Release refused; run privacy check and confirm output directory.',file=sys.stderr)
        sys.exit(1)
