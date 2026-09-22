"""Windows: read required cookies and pass to Node in memory; no credential files.

python scripts/extract_cookies.py <User Data> [--profile Default] list <sessions.json>
python scripts/extract_cookies.py <User Data> [--profile Default] fetch <peerId> <raw.json>
"""
import argparse
from contextlib import closing
import base64
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data):
    if sys.platform != 'win32':
        raise ValueError('DPAPI requires Windows')
    buf = ctypes.create_string_buffer(data)
    incoming = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outgoing = DATA_BLOB()
    decrypt = ctypes.windll.crypt32.CryptUnprotectData
    decrypt.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    decrypt.restype = wintypes.BOOL
    free = ctypes.windll.kernel32.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    if not decrypt(ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)):
        raise ValueError('Windows could not decrypt the key for this account')
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        free(outgoing.pbData)


def read_cookies(user_data, profile='Default'):
    root = Path(user_data).resolve()
    if not profile or Path(profile).name != profile or profile in ('.', '..'):
        raise ValueError('Use a profile folder name, such as Default or Profile 1')
    db = root / profile / 'Network' / 'Cookies'
    if not db.is_file():
        db = root / profile / 'Cookies'
    # No copy or immutable=1: preserve SQLite WAL visibility and fail if locked.
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
        con.execute('PRAGMA query_only=ON')
        version = int(con.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
        cols = {r[1] for r in con.execute('PRAGMA table_info(cookies)')}
        partition = " AND top_frame_site_key=''" if 'top_frame_site_key' in cols else ''
        rows = con.execute(
            "SELECT host_key,name,path,expires_utc,encrypted_value,value FROM cookies "
            "WHERE host_key IN ('music.163.com','.music.163.com','.163.com') "
            "AND name IN ('MUSIC_U','__csrf')" + partition).fetchall()
    now = (time.time() + 11644473600) * 1000000
    cookies = []
    for host, name, path, expiry, enc, plain in rows:
        if path != '/' or (expiry and expiry <= now):
            continue
        c = {'host': host, 'name': name, 'path': path}
        if enc:
            if not bytes(enc).startswith(b'v10'):
                raise ValueError('Unsupported cookie encryption (including v20); no bypass attempted')
            c['ct'] = bytes(enc).hex()
        elif plain:
            c['plain'] = plain
        else:
            continue
        cookies.append(c)
    if not any(c['name'] == 'MUSIC_U' for c in cookies):
        raise ValueError('No unexpired MUSIC_U in selected profile')
    key = b''
    if any('ct' in c for c in cookies):
        state = json.loads((root / 'Local State').read_text(encoding='utf-8'))
        encrypted = base64.b64decode(state['os_crypt']['encrypted_key'], validate=True)
        if not encrypted.startswith(b'DPAPI'):
            raise ValueError('Unsupported browser key format')
        key = dpapi_unprotect(encrypted[5:])
        if len(key) != 32:
            raise ValueError('Invalid browser key length')
    return {'key': key.hex(), 'dbVersion': version, 'cookies': cookies}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('user_data')
    p.add_argument('--profile', default='Default')
    p.add_argument('--node', default='node')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('list').add_argument('output')
    fetch = sub.add_parser('fetch')
    fetch.add_argument('peer_id')
    fetch.add_argument('output')
    args = p.parse_args()
    try:
        raw = read_cookies(args.user_data, args.profile)
        cmd = [args.node, str(Path(__file__).with_name('netease.js')), '--raw-stdin', args.command]
        if args.command == 'fetch':
            cmd.append(args.peer_id)
        cmd.append(args.output)
        # Never print raw, put it in argv, or redirect it to disk.
        return subprocess.run(cmd, input=json.dumps(raw), encoding='utf-8', check=False).returncode
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print('Cookie access failed. Check profile/login, unsupported encryption, or close the browser yourself and retry. No credential copy was created.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
