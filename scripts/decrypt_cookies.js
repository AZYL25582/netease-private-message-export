'use strict';
// Internal module only. No raw/plain credential file output.
const crypto = require('crypto');
const { TextDecoder } = require('util');
const NAMES = new Set(['MUSIC_U', '__csrf']);
const HOSTS = new Set(['music.163.com', '.music.163.com', '.163.com']);

function sanitizeCookies(input) {
  const out = {};
  for (const name of NAMES) {
    const value = input[name];
    if (value == null) continue;
    if (typeof value !== 'string' || !/^[\x21-\x7e]+$/.test(value) || /[;\\",]/.test(value)) {
      throw new Error('Invalid credential value');
    }
    out[name] = value;
  }
  if (!out.MUSIC_U) throw new Error('Missing MUSIC_U');
  return out;
}

function decryptCookies(raw) {
  if (!Array.isArray(raw.cookies) || !Number.isInteger(raw.dbVersion)) throw new Error('Invalid cookie input');
  const key = Buffer.from(raw.key, 'hex');
  const out = {};
  try {
    for (const c of raw.cookies) {
      if (!HOSTS.has(c.host) || !NAMES.has(c.name) || c.path !== '/') continue;
      let value;
      if (c.ct != null) {
        const ct = Buffer.from(c.ct, 'hex');
        if (ct.subarray(0, 3).toString() !== 'v10' || key.length !== 32 || ct.length < 31) {
          throw new Error('Unsupported cookie encryption; no bypass attempted');
        }
        const dec = crypto.createDecipheriv('aes-256-gcm', key, ct.subarray(3, 15));
        dec.setAuthTag(ct.subarray(-16));
        const plain = Buffer.concat([dec.update(ct.subarray(15, -16)), dec.final()]);
        try {
          // Chromium database v24+ prefixes the plaintext with SHA256(host_key).
          const start = raw.dbVersion >= 24 ? 32 : 0;
          if (start && (plain.length < 32 || !crypto.timingSafeEqual(plain.subarray(0, 32), crypto.createHash('sha256').update(c.host).digest()))) {
            throw new Error('Cookie domain binding mismatch');
          }
          value = new TextDecoder('utf-8', {fatal:true}).decode(plain.subarray(start));
        } finally { plain.fill(0); }
      } else { value = c.plain; }
      if (Object.prototype.hasOwnProperty.call(out, c.name) && out[c.name] !== value) throw new Error('Ambiguous cookie values across domains');
      out[c.name] = value;
    }
    return sanitizeCookies(out);
  } finally { key.fill(0); }
}

module.exports = {decryptCookies, sanitizeCookies};
if (require.main === module) {
  console.error('Internal module: use extract_cookies.py to run list/fetch through an in-memory pipe.');
  process.exitCode = 1;
}
