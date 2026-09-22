'use strict';
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const https = require('https');
const {decryptCookies, sanitizeCookies} = require('./decrypt_cookies');
const PRESET_KEY = '0CoJUm6Qyw8W8jud';
const IV = '0102030405060708';
const MODULUS = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function fail(code) { const error = new Error(code); error.safeCode = code; throw error; }
function aes(text, key) {
  const cipher = crypto.createCipheriv('aes-128-cbc', Buffer.from(key), Buffer.from(IV));
  return Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]).toString('base64');
}
function rsa(text) {
  let base = BigInt('0x' + Buffer.from([...text].reverse().join('')).toString('hex'));
  let power = 65537n, result = 1n;
  const modulus = BigInt('0x' + MODULUS);
  while (power) {
    if (power & 1n) result = result * base % modulus;
    base = base * base % modulus;
    power >>= 1n;
  }
  return result.toString(16).padStart(256, '0');
}
function weapi(body) {
  const key = crypto.randomBytes(8).toString('hex');
  return new URLSearchParams({params: aes(aes(JSON.stringify(body), PRESET_KEY), key), encSecKey: rsa(key)}).toString();
}
function id(value) {
  if (typeof value === 'number' && (!Number.isSafeInteger(value) || value <= 0)) fail('unsafe_id');
  if (!/^[1-9][0-9]*$/.test(String(value))) fail('invalid_id');
  return String(value);
}
function makeHttpPost(cookies) {
  const ck = sanitizeCookies(cookies);
  const header = Object.entries(ck).map(([k,v]) => k + '=' + v).join('; ');
  return async (endpoint, body) => {
    if (!['private/users','private/history'].includes(endpoint)) fail('invalid_endpoint');
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const payload = weapi(body);
        const j = await new Promise((resolve, reject) => {
          const req = https.request({hostname:'music.163.com', port:443,
            path:'/weapi/msg/' + endpoint + '?csrf_token=' + encodeURIComponent(ck.__csrf || ''),
            method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded',
              'Content-Length':Buffer.byteLength(payload), Referer:'https://music.163.com/msg/',
              'User-Agent':'Mozilla/5.0', Cookie:header}}, res => {
            const chunks = []; let size = 0;
            res.on('data', chunk => {
              size += chunk.length;
              if (size > 8 * 1024 * 1024) { req.destroy(); reject(new Error('response_limit')); }
              else chunks.push(chunk);
            });
            res.on('error', () => reject(new Error('network_error')));
            res.on('aborted', () => reject(new Error('network_error')));
            res.on('end', () => {
              if (res.statusCode !== 200) { reject(new Error('http_' + res.statusCode)); return; }
              try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
              catch { reject(new Error('invalid_response')); }
            });
          });
          req.setTimeout(20000, () => req.destroy(new Error('timeout')));
          req.on('error', () => reject(new Error('network_error')));
          req.end(payload);
        });
        if (!j || j.code !== 200) {
          if (j && (j.code === 429 || j.code >= 500)) throw new Error('retry_api');
          fail('api_rejected');
        }
        return j;
      } catch (error) {
        if (error.safeCode) throw error;
        if (attempt === 2) fail('request_failed');
        await wait(1000 * (attempt + 1));
      }
    }
  };
}

function outputPath(file) {
  const p = path.resolve(file);
  let ancestor = p;
  while (!fs.existsSync(ancestor)) ancestor = path.dirname(ancestor);
  const resolved = path.join(fs.realpathSync(ancestor), path.relative(ancestor, p));
  const skill = fs.realpathSync(path.join(__dirname, '..'));
  const rel = path.relative(skill, resolved);
  if (!rel || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel))) fail('output_inside_skill');
  if (fs.existsSync(p) && fs.lstatSync(p).isSymbolicLink()) fail('output_symlink');
  return p;
}
function writeJson(file, data) {
  const p = outputPath(file);
  fs.mkdirSync(path.dirname(p), {recursive:true});
  const tmp = p + '.tmp-' + crypto.randomBytes(8).toString('hex');
  try {
    const fd = fs.openSync(tmp, 'wx', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(data, null, 2) + '\n'); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    if (fs.existsSync(p)) {
      const backup = p + '.backup-' + crypto.randomBytes(8).toString('hex');
      const fdBackup = fs.openSync(backup, 'wx', 0o600);
      try { fs.writeFileSync(fdBackup, fs.readFileSync(p)); fs.fsyncSync(fdBackup); }
      finally { fs.closeSync(fdBackup); }
    }
    fs.renameSync(tmp, p);
  } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
}

async function listSessions(post, pause = wait) {
  const rows = new Map();
  for (let page = 0; page < 400; page++) {
    const j = await post('private/users', {limit:100, offset:page*100});
    if (!j || j.code !== 200 || !Array.isArray(j.msgs)) fail('sessions_failed');
    let added = 0;
    for (const m of j.msgs) {
      if (!m.user) fail('session_identity_missing');
      const peer = id(m.user.fromUserId), mine = id(m.user.toUserId);
      if (peer === mine) fail('session_identity_invalid');
      if (rows.has(peer) && rows.get(peer).myId !== mine) fail('session_identity_conflict');
      if (!rows.has(peer)) added++;
      rows.set(peer, {id:peer, myId:mine, nick:m.fromUser?.nickname || peer,
        msgCount:m.user.msgCount, lastMsgTime:m.user.lastMsgTime});
    }
    if (new Set([...rows.values()].map(r=>r.myId)).size > 1) fail('session_identity_conflict');
    if (j.more === false) return [...rows.values()];
    // Some users responses omit more. A short page terminates the offset list only.
    if (j.more == null && j.msgs.length < 100) return [...rows.values()];
    if (!added) fail('sessions_stalled');
    await pause(350);
  }
  fail('sessions_page_limit');
}

async function fetchHistory(post, session, pause = wait, maxPages = 400) {
  const peer = id(session.id), mine = id(session.myId);
  const seen = new Map();
  let cursor = -1, pages = 0, complete = false, stopReason = 'page_limit';
  try {
    for (; pages < maxPages;) {
      const j = await post('private/history', {userId:peer, limit:100, time:cursor});
      pages++;
      if (!j || j.code !== 200 || !Array.isArray(j.msgs)) fail('history_failed');
      let added = 0, earliest = Infinity;
      for (const m of j.msgs) {
        const mid = id(m.id), sender = id(m.fromUser?.userId);
        if (![mine, peer].includes(sender)) fail('unknown_sender');
        if (m.toUser && id(m.toUser.userId) !== (sender === mine ? peer : mine)) fail('recipient_mismatch');
        if (!Number.isSafeInteger(m.time) || m.time <= 0 || m.time > 8640000000000000) fail('invalid_timestamp');
        earliest = Math.min(earliest, m.time);
        let raw = null;
        try { raw = JSON.parse(m.msg); } catch {}
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) raw = null;
        if (raw?.refMsgId != null) raw.refMsgId = id(raw.refMsgId);
        const rec = {id:mid,time:m.time,from:sender === mine ? '我' : '对方',fromId:sender,
          type:raw?.type || 0,text:typeof raw?.msg === 'string' ? raw.msg : (raw ? '' : String(m.msg || '')),raw};
        if (seen.has(mid)) {
          if (JSON.stringify(seen.get(mid)) !== JSON.stringify(rec)) fail('message_conflict');
        } else { seen.set(mid,rec); added++; }
      }
      if (j.more === false) { complete = true; stopReason = 'more_false'; break; }
      if (j.more !== true) fail('missing_more');
      if (!j.msgs.length) fail('empty_page_with_more');
      if (!added || (cursor !== -1 && earliest >= cursor)) fail('cursor_stalled');
      cursor = earliest;
      await pause(350);
    }
  } catch (e) {
    // Identity/schema corruption is not a usable partial export. Main exits 1
    // without publishing any current-run message data or replacing old results.
    if (['unknown_sender','recipient_mismatch','message_conflict','invalid_id','unsafe_id','invalid_timestamp'].includes(e.safeCode)) throw e;
    stopReason = e.safeCode || 'request_failed';
  }
  const messages = [...seen.values()].sort((a,b)=>a.time-b.time || (BigInt(a.id)<BigInt(b.id)?-1:BigInt(a.id)>BigInt(b.id)?1:0));
  return {schemaVersion:2, nick:session.nick,id:peer,myId:mine,complete,stopReason,pages,
    nextCursor:complete ? null : cursor, fetchedAt:new Date().toISOString(),count:messages.length,messages};
}

async function browserCookies(profileDir) {
  const {chromium} = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
  const options = {headless:true, chromiumSandbox:true, args:['--disable-extensions']};
  if (process.env.EDGE_PATH) options.executablePath = process.env.EDGE_PATH;
  else options.channel = 'msedge';
  const context = await chromium.launchPersistentContext(profileDir, options);
  try {
    const out = {};
    for (const c of await context.cookies('https://music.163.com/weapi/msg/private/history')) {
      if (!['MUSIC_U','__csrf'].includes(c.name) || !['music.163.com','.music.163.com','.163.com'].includes(c.domain) || c.path !== '/') continue;
      if (c.expires > 0 && c.expires <= Date.now()/1000) continue;
      if (out[c.name] && out[c.name] !== c.value) fail('ambiguous_cookies');
      out[c.name] = c.value;
    }
    return sanitizeCookies(out);
  } finally { await context.close(); }
}
async function readStdin() {
  const chunks = []; let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 1024*1024) fail('credential_input_limit');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
async function main(args = process.argv.slice(2)) {
  let source = args.shift(), profile;
  if (source === '--browser') { profile = args.shift(); source = '--browser'; }
  const [cmd, a, b] = args;
  if (!source || !['list','fetch'].includes(cmd) || !a || (cmd === 'fetch' && !b) || args.length !== (cmd === 'fetch'?3:2)) {
    fail('usage: netease.js <cookies.json|--raw-stdin|--browser profile> list <out.json> OR fetch <peerId> <out.json>');
  }
  if (cmd === 'fetch') id(a);
  const output = outputPath(cmd === 'fetch' ? b : a);
  if (!source.startsWith('--') && path.resolve(source) === output) fail('output_is_credential_input');
  let cookies;
  if (source === '--raw-stdin') cookies = decryptCookies(await readStdin());
  else if (source === '--browser') cookies = await browserCookies(profile);
  else cookies = sanitizeCookies(JSON.parse(fs.readFileSync(source,'utf8')));
  const post = makeHttpPost(cookies);
  const sessions = await listSessions(post);
  if (cmd === 'list') { writeJson(output,{schemaVersion:2,sessions}); console.log('Session list saved; rows=' + sessions.length); return 0; }
  const session = sessions.find(s=>s.id === id(a));
  if (!session) fail('peer_not_found_identity_unconfirmed');
  const result = await fetchHistory(post, session);
  writeJson(result.complete ? output : output + '.partial.json',result);
  if (!result.complete) { console.error('PARTIAL: ' + result.stopReason + '; previous complete output preserved'); return 2; }
  console.log('DONE: server pagination ended; messages=' + result.count);
  return 0;
}
module.exports = {id, weapi, makeHttpPost, outputPath, writeJson, listSessions, fetchHistory, main};
if (require.main === module) main().then(code=>{process.exitCode=code;}).catch(e=>{
  console.error('FAILED: ' + (e.safeCode || 'operation_failed (no credentials or response body logged)'));
  process.exitCode=1;
});
