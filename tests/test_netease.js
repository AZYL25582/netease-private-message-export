'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const EventEmitter = require('events');
const api = require('../scripts/netease');
const auth = require('../scripts/decrypt_cookies');
https.request = () => { throw new Error('Real network forbidden in tests'); };
const pause = async()=>{};
const session = {id:'33',myId:'22',nick:'SYNTHETIC-PEER'};
const msg = (id=1,time=1700000000000) => ({id,time,fromUser:{userId:22},toUser:{userId:33},msg:JSON.stringify({type:6,msg:'SYNTHETIC-TEXT'})});
const page = (more=true) => ({code:200,more,msgs:[msg()]});
const queue = replies => async()=> {assert.ok(replies.length); const r=replies.shift(); if(r instanceof Error) throw r; return r;};

test('only required cookies reach HTTPS request headers',async()=>{
  let captured;
  const previous=https.request;
  https.request=(options,cb)=>{
    captured=options;
    const request=new EventEmitter(); request.setTimeout=()=>{};
    request.end=()=>{const response=new EventEmitter(); response.statusCode=200; cb(response);
      response.emit('data',Buffer.from('{"code":200,"msgs":[],"more":false}')); response.emit('end');};
    return request;
  };
  try {await api.makeHttpPost({MUSIC_U:'SYNTHETIC-MUSIC',__csrf:'SYNTHETIC-CSRF',MAIL_SECRET:'SYNTHETIC-MAIL'})('private/users',{});}
  finally {https.request=previous;}
  assert.equal(captured.hostname,'music.163.com'); assert.ok(!captured.headers.Cookie.includes('MAIL_SECRET'));
  assert.throws(()=>auth.sanitizeCookies({MUSIC_U:'SYNTHETIC; injection'}));
});

test('v10 and database v24 domain-bound decryption, v20 rejection',()=>{
  const key=crypto.randomBytes(32),nonce=crypto.randomBytes(12),host='.music.163.com';
  const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);
  const plain=Buffer.concat([crypto.createHash('sha256').update(host).digest(),Buffer.from('SYNTHETIC-MUSIC')]);
  const enc=Buffer.concat([Buffer.from('v10'),nonce,cipher.update(plain),cipher.final(),cipher.getAuthTag()]);
  const raw={key:key.toString('hex'),dbVersion:24,cookies:[{host,name:'MUSIC_U',path:'/',ct:enc.toString('hex')}]};
  assert.equal(auth.decryptCookies(raw).MUSIC_U,'SYNTHETIC-MUSIC');
  raw.cookies[0].host='.163.com'; assert.throws(()=>auth.decryptCookies(raw));
  raw.cookies[0].ct=Buffer.from('v20unsupported').toString('hex'); assert.throws(()=>auth.decryptCookies(raw));
});

test('cross-domain and ambiguous cookies rejected or discarded',()=>{
  const raw={key:'',dbVersion:24,cookies:[{host:'mail.163.com',path:'/',name:'MUSIC_U',plain:'SYNTHETIC-MAIL'}]};
  assert.throws(()=>auth.decryptCookies(raw));
  raw.cookies=[{host:'.music.163.com',path:'/',name:'MUSIC_U',plain:'SYNTHETIC-A'},
    {host:'.163.com',path:'/',name:'MUSIC_U',plain:'SYNTHETIC-B'}];
  assert.throws(()=>auth.decryptCookies(raw));
});

test('HTTP/API failure after first page remains partial',async()=>{
  for(const failure of [{code:500},new Error('SYNTHETIC-NETWORK')]) {
    const d=await api.fetchHistory(queue([page(),failure]),session,pause);
    assert.equal(d.complete,false); assert.equal(d.count,1); assert.notEqual(d.stopReason,'more_false');
  }
});

test('empty page, missing flag, cursor stall and cap never claim complete',async()=>{
  for(const response of [{code:200,more:true,msgs:[]},{code:200,msgs:[]},page()]) {
    const d=await api.fetchHistory(queue([page(),response]),session,pause);
    assert.equal(d.complete,false);
  }
  assert.equal((await api.fetchHistory(queue([page()]),session,pause,1)).stopReason,'page_limit');
});

test('explicit end produces complete history, including an empty conversation',async()=>{
  assert.equal((await api.fetchHistory(queue([page(false)]),session,pause)).complete,true);
  const empty=await api.fetchHistory(queue([{code:200,more:false,msgs:[]}]),session,pause);
  assert.equal(empty.complete,true); assert.equal(empty.count,0);
});

test('unknown sender, recipient, unsafe IDs fail closed',async()=>{
  for(const m of [{...msg(),fromUser:{userId:77}}, {...msg(),toUser:{userId:77}}, {...msg(),id:Number.MAX_SAFE_INTEGER+1}]) {
    await assert.rejects(()=>api.fetchHistory(queue([{code:200,more:false,msgs:[m]}]),session,pause));
  }
  assert.throws(()=>api.id(0)); assert.throws(()=>api.id(NaN));
});

test('session pagination discovers later peer and rejects conflicting identities',async()=>{
  const row=(peer,mine=22)=>({user:{fromUserId:peer,toUserId:mine},fromUser:{nickname:'SYNTHETIC'}});
  const sessions=await api.listSessions(queue([{code:200,msgs:[row(33)],more:true},{code:200,msgs:[row(44)],more:false}]),pause);
  assert.equal(sessions.length,2); assert.equal(sessions[1].id,'44');
  await assert.rejects(()=>api.listSessions(queue([{code:200,msgs:[row(33),row(44,55)],more:false}]),pause));
});

test('atomic writes preserve old result and maintain backups; skill outputs refused',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'netease-synthetic-'));
  try {
    const target=path.join(root,'raw.json'); api.writeJson(target,{complete:true});
    api.writeJson(target+'.partial.json',{complete:false});
    assert.equal(JSON.parse(fs.readFileSync(target)).complete,true);
    api.writeJson(target,{complete:true,count:2});
    assert.ok(fs.readdirSync(root).some(f=>f.startsWith('raw.json.backup-')));
    assert.throws(()=>api.outputPath(path.join(__dirname,'runtime.json')));
  } finally {fs.rmSync(root,{recursive:true});}
});

test('command entrypoint preserves complete file on partial fetch, and stops on missing peer',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'netease-synthetic-cli-'));
  const credential=path.join(root,'synthetic.json'), output=path.join(root,'raw.json');
  const oldRequest=https.request, oldLog=console.log, oldError=console.error;
  const logs=[]; console.log=s=>logs.push(s); console.error=s=>logs.push(s);
  let replies=[];
  https.request=(options,cb)=>{
    assert.ok(replies.length,'Unexpected request');
    const reply=replies.shift(),req=new EventEmitter(); req.setTimeout=()=>{};
    req.end=()=>{const res=new EventEmitter();res.statusCode=200;cb(res);
      res.emit('data',Buffer.from(JSON.stringify(reply)));res.emit('end');};
    return req;
  };
  try {
    fs.writeFileSync(credential,JSON.stringify({MUSIC_U:'SYNTHETIC-MUSIC'}));
    fs.writeFileSync(output,JSON.stringify({complete:true,marker:'SYNTHETIC-OLD'}));
    replies=[{code:200,more:false,msgs:[{user:{fromUserId:33,toUserId:22},fromUser:{nickname:'SYNTHETIC-PRIVATE-NICK'}}]},
      page(),{code:200,more:true,msgs:[]}];
    assert.equal(await api.main([credential,'fetch','33',output]),2);
    assert.equal(JSON.parse(fs.readFileSync(output)).marker,'SYNTHETIC-OLD');
    assert.equal(JSON.parse(fs.readFileSync(output+'.partial.json')).complete,false);
    assert.ok(!logs.some(s=>s.includes('DONE')||s.includes('SYNTHETIC-MUSIC')||s.includes('SYNTHETIC-PRIVATE-NICK')));
    const priorPartial=fs.readFileSync(output+'.partial.json');
    replies=[{code:200,more:false,msgs:[{user:{fromUserId:33,toUserId:22},fromUser:{nickname:'SYNTHETIC-PRIVATE-NICK'}}]},
      page(),{code:200,more:false,msgs:[{...msg(2),fromUser:{userId:77}}]}];
    await assert.rejects(()=>api.main([credential,'fetch','33',output]),/unknown_sender/);
    assert.deepEqual(fs.readFileSync(output+'.partial.json'),priorPartial);
    assert.equal(JSON.parse(fs.readFileSync(output)).marker,'SYNTHETIC-OLD');
    replies=[{code:200,more:false,msgs:[]}];
    await assert.rejects(()=>api.main([credential,'fetch','33',output]),/identity_unconfirmed/);
    assert.equal(replies.length,0);
  } finally {
    https.request=oldRequest;console.log=oldLog;console.error=oldError;
    fs.rmSync(root,{recursive:true});
  }
});
