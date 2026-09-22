"""Synthetic offline regressions. No real account, browser, credentials or network."""
import importlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import extract_cookies as cookies
import download_images as images
import check_privacy as privacy
import local_io
export = importlib.import_module('export')


def message(mid=1,text='SYNTHETIC-TEXT',ref=None):
    return {'id':mid,'time':1700000000000+mid*1000,'from':'我','fromId':22,'type':6,
            'text':text,'raw':{'refMsgId':ref} if ref else {}}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='netease-synthetic-test-')
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        # Any accidental real socket is a test failure.
        self.net = patch('socket.socket',side_effect=AssertionError('Network forbidden in tests'))
        self.net.start(); self.addCleanup(self.net.stop)

    def put(self,name,data):
        p=self.p/name; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8'); return p

    def raw(self,name='raw.json',peer=33,messages=None,complete=True):
        return self.put(name,{'myId':22,'id':peer,'nick':'SYNTHETIC-PEER','complete':complete,
                             'stopReason':'more_false','messages':messages if messages is not None else [message()]})

    def cookie_db(self, encrypted=b''):
        db=self.p/'profile'/'Default'/'Network'/'Cookies'; db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE meta(key TEXT,value TEXT)')
            con.execute("INSERT INTO meta VALUES ('version','24')")
            con.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,path TEXT,expires_utc INTEGER,encrypted_value BLOB,value TEXT,top_frame_site_key TEXT)')
            con.executemany('INSERT INTO cookies VALUES (?,?,?,?,?,?,?)',[
                ('.music.163.com','MUSIC_U','/',0,encrypted,'SYNTHETIC-MUSIC',''),
                ('mail.163.com','MUSIC_U','/',0,b'','SYNTHETIC-MAIL',''),
                ('evil163.com','MUSIC_U','/',0,b'','SYNTHETIC-EVIL',''),
                ('.music.163.com','OTHER','/',0,b'','SYNTHETIC-OTHER',''),
                ('.music.163.com','__csrf','/private',0,b'','SYNTHETIC-PATH',''),
                ('.music.163.com','__csrf','/',1,b'','SYNTHETIC-EXPIRED',''),
                ('.music.163.com','__csrf','/',0,b'','SYNTHETIC-PARTITION','https://other.invalid'),
            ])
            con.commit()
        return db

    def test_cookie_scope_and_no_copy(self):
        db=self.cookie_db(); before=db.read_bytes()
        with patch.object(cookies,'dpapi_unprotect',side_effect=AssertionError('No key needed')):
            result=cookies.read_cookies(self.p/'profile')
        self.assertEqual([c['plain'] for c in result['cookies']],['SYNTHETIC-MUSIC'])
        self.assertEqual(result['key'],'')
        self.assertEqual(before,db.read_bytes())
        self.assertEqual([p.name for p in self.p.rglob('*') if p.is_file()],['Cookies'])

    def test_v20_rejected_before_key_access(self):
        self.cookie_db(b'v20'+b'X'*40)
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            cookies.read_cookies(self.p/'profile')

    def test_profile_traversal_rejected(self):
        with self.assertRaises(ValueError): cookies.read_cookies(self.p,'../Default')

    def test_partial_rejected_and_existing_preserved(self):
        source=self.raw(); out=self.p/'out'
        export.export_data(source,out)
        before=(out/'netease_messages.json').read_bytes()
        partial=self.raw('partial.json',complete=False)
        with self.assertRaises(ValueError): export.export_data(partial,out)
        with self.assertRaises(ValueError): export.export_data(partial,out,allow_incomplete=True)
        self.assertEqual(before,(out/'netease_messages.json').read_bytes())
        d=export.export_data(partial,self.p/'partial-out',allow_incomplete=True)
        self.assertFalse(d['complete'])

    def test_cross_peer_and_account_merge_rejected(self):
        out=self.p/'out'; export.export_data(self.raw(),out)
        before=(out/'netease_messages.json').read_bytes()
        with self.assertRaises(ValueError): export.export_data(self.raw('other.json',peer=44),out,append=True)
        other=json.loads(self.raw().read_text(encoding='utf-8')); other['myId']=55
        with self.assertRaises(ValueError): export.export_data(self.put('account.json',other),out,append=True)
        self.assertEqual(before,(out/'netease_messages.json').read_bytes())

    def test_refs_resolve_after_merge_and_no_false_offset(self):
        out=self.p/'out'; export.export_data(self.raw(),out)
        reply=self.raw('reply.json',messages=[message(3,ref=1),message(4,ref=2)])
        d=export.export_data(reply,out,append=True); msgs=d['sessions'][0]['messages']
        self.assertEqual(msgs[1]['refId'],'1')
        self.assertTrue(msgs[2]['refUnresolved'])
        self.assertEqual(msgs[2]['refCandidateId'],'1')
        self.assertNotIn('refText',msgs[2])

    def test_sorted_empty_and_identity(self):
        source=self.raw(messages=[message(2),message(1)])
        d=export.export_data(source,self.p/'out')
        self.assertEqual([m['id'] for m in d['sessions'][0]['messages']],['1','2'])
        d=export.export_data(self.raw('empty.json',messages=[]),self.p/'empty')
        self.assertEqual(d['sessions'][0]['count'],0)
        bad=message(); bad['fromId']=77
        with self.assertRaises(ValueError): export.export_data(self.raw('bad.json',messages=[bad]),self.p/'bad')

    def test_backup_and_atomic_failure(self):
        dest=self.p/'artifact'; local_io.atomic_write(dest,'OLD')
        local_io.atomic_write(dest,'NEW')
        self.assertEqual(list(self.p.glob('artifact.backup-*'))[0].read_text(),'OLD')
        with patch.object(local_io.os,'replace',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): local_io.atomic_write(dest,'BROKEN')
        self.assertEqual(dest.read_text(),'NEW')
        self.assertFalse(list(self.p.glob('.artifact.*')))

    def test_outputs_inside_skill_rejected(self):
        with self.assertRaises(ValueError): local_io.output_path(ROOT/'runtime.json')

    def test_url_scope_and_tls(self):
        self.assertTrue(images.CTX.check_hostname)
        self.assertNotEqual(images.CTX.verify_mode,0)
        for u in ['file:///synthetic.txt','http://attacker.invalid/a.jpg','https://127.0.0.1/a',
                  'https://p1.music.126.net.attacker.invalid/a','https://user@p1.music.126.net/a',
                  'https://p1.music.126.net:444/a']:
            with self.subTest(url=u),self.assertRaises(ValueError): images.fetch(u)

    def test_known_http_upgrades_before_any_connection(self):
        u=images.validate_url('http://p1.music.126.net:80/synthetic.jpg?x=1')
        self.assertEqual(u.geturl(),'https://p1.music.126.net/synthetic.jpg?x=1')
        for url in ['http://p1.music.126.net:8080/a','http://p1.music.126.net:443/a',
                    'http://@p1.music.126.net/a','http://p1.music.126.net.attacker.invalid/a']:
            with self.subTest(url=url),self.assertRaises(ValueError): images.validate_url(url)

    def test_http_jpg_alias_download_uses_only_verified_https(self):
        data=b'\xff\xd8\xff'+b'SYNTHETIC'*32
        response=Mock(status=200)
        response.getheader.side_effect=lambda name: {'Content-Length':str(len(data)),
            'Content-Type':'image/jpg; charset=binary'}.get(name)
        response.read.return_value=data
        conn=Mock(); conn.getresponse.return_value=response
        with patch.object(images,'PinnedHTTPS',return_value=conn) as connect:
            self.assertEqual(images.fetch('http://p2.music.126.net/synthetic.jpg'),data)
            connect.assert_called_once_with('p2.music.126.net',443,timeout=20,context=images.CTX)
        self.assertTrue(images.CTX.check_hostname)
        response.read.return_value=b'\x89PNG\r\n\x1a\n'+b'SYNTHETIC'*32
        response.getheader.side_effect=lambda name: 'image/jpg' if name=='Content-Type' else None
        with patch.object(images,'PinnedHTTPS',return_value=conn):
            self.assertEqual(images.fetch('http://p2.music.126.net/synthetic.jpg'),response.read.return_value)
        response.read.return_value=b'<html>SYNTHETIC-ERROR</html>'
        with patch.object(images,'PinnedHTTPS',return_value=conn):
            with self.assertRaises(ValueError): images.fetch('http://p2.music.126.net/synthetic.jpg')

    def test_mislabeled_png_saved_as_png_without_modifying_input(self):
        m=message(); m['type']=16; m['raw']={'picInfo':{'picUrl':'http://p1.music.126.net/synthetic.jpg'}}
        raw=self.raw(messages=[m]); before=raw.read_bytes(); out=self.p/'out'
        export.export_data(raw,out)
        data=b'\x89PNG\r\n\x1a\n'+b'SYNTHETIC'*32
        response=Mock(status=200)
        response.getheader.side_effect=lambda name: {'Content-Type':'image/jpg','Content-Length':str(len(data))}.get(name)
        response.read.return_value=data
        conn=Mock(); conn.getresponse.return_value=response
        with patch.object(images,'PinnedHTTPS',return_value=conn):
            self.assertEqual(images.download(out/'netease_messages.json'),0)
        d=json.loads((out/'netease_messages.json').read_text(encoding='utf-8'))
        saved=d['sessions'][0]['messages'][0]
        self.assertTrue(saved['imageFile'].endswith('.png'))
        self.assertEqual((out/'images'/saved['imageFile']).read_bytes(),data)
        self.assertEqual(raw.read_bytes(),before)

    def test_redirect_http_target_is_upgraded_and_external_target_rejected(self):
        first=Mock(status=302)
        first.getheader.side_effect=lambda name: 'http://p2.music.126.net/next.jpg' if name=='Location' else None
        data=b'\xff\xd8\xff'+b'SYNTHETIC'*32
        second=Mock(status=200)
        second.getheader.side_effect=lambda name: 'image/jpeg' if name=='Content-Type' else None
        second.read.return_value=data
        a,b=Mock(),Mock(); a.getresponse.return_value=first; b.getresponse.return_value=second
        with patch.object(images,'PinnedHTTPS',side_effect=[a,b]) as connect:
            self.assertEqual(images.fetch('http://p1.music.126.net/a.jpg'),data)
            self.assertEqual([call.args[:2] for call in connect.call_args_list],
                             [('p1.music.126.net',443),('p2.music.126.net',443)])
        first.getheader.side_effect=lambda name: 'http://attacker.invalid/a.jpg' if name=='Location' else None
        with patch.object(images,'PinnedHTTPS',return_value=a) as connect:
            with self.assertRaises(ValueError): images.fetch('https://p1.music.126.net/a.jpg')
            self.assertEqual(connect.call_count,1)

    def test_private_dns_rejected_before_connect(self):
        row=(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))
        with patch.object(images.socket,'getaddrinfo',return_value=[row]):
            with self.assertRaises(ValueError): images.PinnedHTTPS('p1.music.126.net').connect()

    def test_redirect_media_and_size_limits(self):
        response=Mock(status=302)
        response.getheader.side_effect=lambda name: 'file:///synthetic.txt' if name=='Location' else None
        conn=Mock(); conn.getresponse.return_value=response
        with patch.object(images,'PinnedHTTPS',return_value=conn):
            with self.assertRaises(ValueError): images.fetch('https://p1.music.126.net/a')
        response.status=200
        response.getheader.side_effect=lambda name: str(images.MAX_BYTES+1) if name=='Content-Length' else 'image/png'
        with patch.object(images,'PinnedHTTPS',return_value=conn):
            with self.assertRaises(ValueError): images.fetch('https://p1.music.126.net/a')
        with self.assertRaises(ValueError): images.image_extension(b'<html>synthetic</html>')

    def test_image_roundtrip_metadata(self):
        m=message(); m['type']=16; m['raw']={'picInfo':{'picUrl':'https://p1.music.126.net/synthetic.png'}}
        raw=self.raw(messages=[m]); out=self.p/'out'; export.export_data(raw,out)
        data=b'\x89PNG\r\n\x1a\n'+b'SYNTHETIC'*32
        with patch.object(images,'fetch',return_value=data):
            self.assertEqual(images.download(out/'netease_messages.json',out/'custom'),0)
        with patch.object(images,'fetch',side_effect=AssertionError('Already downloaded')):
            self.assertEqual(images.download(out/'netease_messages.json',out/'custom'),0)
        d=export.export_data(raw,out,append=True)
        self.assertEqual(d['imageDir'],'custom'); self.assertEqual(d['imageDownloaded'],1)
        self.assertIn('custom/',(out/'netease_messages.txt').read_text(encoding='utf-8'))
        self.assertNotIn('imageErr',d['sessions'][0]['messages'][0])

    @unittest.skipUnless(sys.platform=='win32','Windows DPAPI only')
    def test_windows_dpapi_synthetic_roundtrip(self):
        import ctypes
        from ctypes import wintypes
        data=b'SYNTHETIC-KEY-MATERIAL'
        buf=ctypes.create_string_buffer(data)
        incoming=cookies.DATA_BLOB(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_char)))
        outgoing=cookies.DATA_BLOB()
        protect=ctypes.windll.crypt32.CryptProtectData
        protect.argtypes=[ctypes.POINTER(cookies.DATA_BLOB),ctypes.c_void_p,ctypes.c_void_p,
                          ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(cookies.DATA_BLOB)]
        protect.restype=wintypes.BOOL
        self.assertTrue(protect(ctypes.byref(incoming),None,None,None,None,0,ctypes.byref(outgoing)))
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p]; free.restype=ctypes.c_void_p
        try:
            encrypted=ctypes.string_at(outgoing.pbData,outgoing.cbData)
        finally:
            free(outgoing.pbData)
        self.assertEqual(cookies.dpapi_unprotect(encrypted),data)

    def test_privacy_rejects_all_old_blind_spots(self):
        root=self.p/'release'; shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('__pycache__','.git'))
        self.assertEqual(privacy.scan(root)[0],[])
        for name in ['renamed_credentials.json','private_photo.jpg','Cookies','Local State','netease_sessions.json']:
            p=root/name; p.write_bytes(b'SYNTHETIC')
            self.assertTrue(any(name==where for where,_ in privacy.scan(root)[0])); p.unlink()
        # Renaming a credential into a permitted text path must still fail.
        with (root/'README.md').open('a',encoding='utf-8') as f:
            fake_credential = 'NOT-A-REAL-CREDENTIAL'
            f.write('\n'+json.dumps({'MUSIC_U':fake_credential}))
        self.assertTrue(any(reason=='credential-like literal' for _,reason in privacy.scan(root)[0]))


if __name__ == '__main__': unittest.main()
