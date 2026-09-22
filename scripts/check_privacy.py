"""Check release allowlist and configured privacy rules; passing is not a privacy guarantee."""
import argparse
import re
from pathlib import Path
import sys

RELEASE_FILES = {
    '.gitignore','LICENSE','README.md','README_EN.md','SKILL.md',
    'examples/cookies.example.json','references/security-and-limitations.md',
    'scripts/extract_cookies.py','scripts/decrypt_cookies.js','scripts/netease.js',
    'scripts/export.py','scripts/download_images.py','scripts/local_io.py',
    'scripts/check_privacy.py','scripts/package_release.py',
    'tests/test_security.py','tests/test_netease.js',
}
IGNORED_DIRS = {'.git','__pycache__'}  # Never included by package_release.py.
CREDENTIAL = re.compile(r'''["']?(?:MUSIC_U|__csrf|encrypted_key|app_bound_encrypted_key)["']?\s*[:=]\s*["']([^"'\r\n]+)["']''')
MASTER_KEY = re.compile(r'''["']key["']\s*:\s*["']([0-9a-fA-F]{64})["']''')
USER_PATH = re.compile(r'[A-Za-z]:[\\/]Users[\\/](?![<$%])[^\\/\s"\']+')
PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')


def is_placeholder(value):
    return value.startswith(('SYNTHETIC','<')) or set(value)=={'0'}


def scan(root, words=()):
    root = Path(root)
    if not root.is_dir(): return [('root','directory missing')],[]
    hits, skipped = [],[]
    present = set()
    def walk(folder):
        for p in sorted(folder.iterdir()):
            rel = p.relative_to(root).as_posix()
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):
                hits.append((rel,'linked path is not permitted')); continue
            if p.is_dir():
                if p.name in IGNORED_DIRS:
                    skipped.append(rel); continue
                if not any(f.startswith(rel+'/') for f in RELEASE_FILES):
                    hits.append((rel,'directory outside release allowlist')); continue
                walk(p)
                continue
            present.add(rel)
            if rel not in RELEASE_FILES:
                hits.append((rel,'file outside release allowlist')); continue
            try:
                if p.stat().st_size>512*1024: raise ValueError()
                text = p.read_text(encoding='utf-8-sig')
            except (ValueError,OSError,UnicodeError):
                hits.append((rel,'unreadable, oversized or binary file')); continue
            for line_no,line in enumerate(text.splitlines(),1):
                where = '%s:%d' % (rel,line_no)
                if any(not is_placeholder(m.group(1)) for m in CREDENTIAL.finditer(line)):
                    hits.append((where,'credential-like literal'))
                if MASTER_KEY.search(line): hits.append((where,'plaintext master key literal'))
                if USER_PATH.search(line): hits.append((where,'personal user path'))
                if PHONE.search(line): hits.append((where,'phone-like number'))
                if any(w and w in line for w in words): hits.append((where,'configured sensitive word'))
    walk(root)
    for missing in sorted(RELEASE_FILES-present): hits.append((missing,'required release file missing'))
    return hits,skipped


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',nargs='?',default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--words',default='')
    p.add_argument('--quiet',action='store_true',help='Compatibility flag; matched contents are always hidden')
    a = p.parse_args()
    hits,skipped = scan(a.root,a.words.split(','))
    for filename,reason in hits: print('%s: %s' % (filename,reason))
    for directory in skipped: print('Not inspected or packaged: '+directory)
    print('FAIL: %d findings' % len(hits) if hits else 'PASS: no configured rule matched; this does not prove absence of all personal data.')
    return 1 if hits else 0


if __name__ == '__main__': sys.exit(main())
