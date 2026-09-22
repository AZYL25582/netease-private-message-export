"""Download HTTPS images from a small CDN allowlist, with verified TLS and pinned public IPs."""
import argparse
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import sys
from urllib.parse import urlsplit, urljoin
from local_io import atomic_write, output_path, write_json

CTX = ssl.create_default_context()
MAX_BYTES = 20 * 1024 * 1024
HOST = re.compile(r'(?:p[1-9][0-9]*\.music\.126\.net|p[1-9][0-9]*c\.music\.126\.net)\Z',re.I)
ALLOWED_MIME = {'image/jpeg','image/jpg','image/png','image/gif','image/webp'}


def validate_url(url):
    if not isinstance(url,str) or any(ord(c)<33 or ord(c)>126 for c in url) or '\\' in url:
        raise ValueError('Invalid image URL')
    u = urlsplit(url)
    if not HOST.fullmatch(u.hostname or '') or u.username is not None or u.password is not None or u.fragment:
        raise ValueError('Only approved image hosts without userinfo or fragments are allowed')
    if u.scheme == 'http' and u.port in (None,80):
        # Upgrade locally before DNS or network access; never request plaintext HTTP.
        u = u._replace(scheme='https',netloc=u.hostname)
    elif u.scheme != 'https' or u.port not in (None,443):
        raise ValueError('Only HTTPS on port 443 or upgradable standard HTTP URLs are allowed')
    return u


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        addresses = socket.getaddrinfo(self.host,self.port,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError('Non-public image destination refused')
        # Connect to the checked numeric address; do not resolve the host a second time.
        last = None
        for family,kind,proto,_,address in addresses:
            sock = socket.socket(family,kind,proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(address)
                self.sock = CTX.wrap_socket(sock,server_hostname=self.host)
                return
            except OSError as e:
                last = e
                sock.close()
        raise last or OSError('Image connection failed')


def image_extension(data):
    if data.startswith(b'\xff\xd8\xff'): return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png'
    if data.startswith((b'GIF87a',b'GIF89a')): return 'gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'webp'
    raise ValueError('Response is not a supported raster image')


def fetch(url):
    for redirects in range(4):
        u = validate_url(url)
        url = u.geturl()
        conn = PinnedHTTPS(u.hostname,443,timeout=20,context=CTX)
        try:
            target = (u.path or '/') + ('?' + u.query if u.query else '')
            conn.request('GET',target,
                         headers={'User-Agent':'Mozilla/5.0','Referer':'https://music.163.com/'})
            r = conn.getresponse()
            if r.status in (301,302,303,307,308):
                if redirects == 3 or not r.getheader('Location'):
                    raise ValueError('Image redirect limit')
                url = urljoin(url,r.getheader('Location'))
                validate_url(url)
                continue
            if r.status != 200: raise ValueError('Image HTTP failure')
            length = r.getheader('Content-Length')
            if length and (not length.isdigit() or int(length)>MAX_BYTES): raise ValueError('Image size limit')
            content_type = (r.getheader('Content-Type') or '').split(';')[0].strip().lower()
            if content_type not in ALLOWED_MIME:
                raise ValueError('Unsupported image content type')
            data = r.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES: raise ValueError('Image size limit')
            if length and len(data)!=int(length): raise ValueError('Truncated image response')
            # CDN metadata can label PNG as image/jpg. Check both independently;
            # the supported file signature determines the saved extension.
            image_extension(data)
            return data
        finally:
            conn.close()
    raise ValueError('Image redirect limit')


def download(json_file, image_dir=None):
    source = output_path(json_file)
    d = json.loads(source.read_text(encoding='utf-8-sig'))
    if len(d.get('sessions',[])) != 1: raise ValueError('Exactly one session is required')
    root = source.parent.resolve()
    image_dir = output_path(image_dir or root/'images').resolve()
    if root not in image_dir.parents: raise ValueError('Image directory must be inside the export directory')
    image_dir.mkdir(parents=True,exist_ok=True)
    manifest = []
    for m in d['sessions'][0]['messages']:
        if not m.get('picUrl'): continue
        row = {'msgId':m['id'],'ok':False}
        try:
            validate_url(m['picUrl'])
            data = None
            old_name = m.get('imageFile','')
            if (d.get('imageDir') == image_dir.relative_to(root).as_posix()
                    and re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|png|gif|webp)',old_name)
                    and m.get('imageSHA256') == old_name.split('.')[0]):
                old_file = output_path(image_dir/old_name)
                if old_file.is_file() and old_file.stat().st_size <= MAX_BYTES:
                    prior = old_file.read_bytes()
                    if len(prior)==m.get('imageBytes') and hashlib.sha256(prior).hexdigest()==m['imageSHA256']:
                        data = prior
            if data is None:
                data = fetch(m['picUrl'])
            digest = hashlib.sha256(data).hexdigest()
            # Filename depends on content rather than mutable message sequence numbers.
            name = digest + '.' + image_extension(data)
            dest = image_dir/name
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:
                atomic_write(dest,data)
            m.update(imageFile=name,imageBytes=len(data),imageSHA256=digest)
            m.pop('imageErr',None)
            row.update(ok=True,file=name,bytes=len(data),sha256=digest)
        except (OSError,ValueError,http.client.HTTPException):
            # No URLs, credential-like query strings, server bodies or local paths in errors.
            m['imageErr'] = 'image_download_failed_or_refused'
            row['error'] = m['imageErr']
        manifest.append(row)
    d['imageDir'] = image_dir.relative_to(root).as_posix()
    d['imageDownloaded'] = sum(r['ok'] for r in manifest)
    d['imageFailed'] = len(manifest)-d['imageDownloaded']
    write_json(root/'images_manifest.json',manifest)
    write_json(source,d)
    return d['imageFailed']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('json_file'); p.add_argument('image_dir',nargs='?')
    a = p.parse_args()
    try:
        failures = download(a.json_file,a.image_dir)
        print('Images processed; failed=%d; rerun export --append to refresh TXT' % failures)
        return 2 if failures else 0
    except (OSError,ValueError,KeyError,TypeError):
        print('Image download refused: check schema and output directory.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
