// Optional browser smoke check. Requires Node 22+, prototype on :8765 and an
// isolated headless Chrome with remote debugging on :9229. No npm dependencies.
// Never point this check at a personal browser profile: it clears this origin's prototype storage.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const tabs = await (await fetch('http://127.0.0.1:9229/json/list')).json();
  const tab = tabs.find(t => t.type === 'page');
  assert(tab, 'A browser page must exist');
  const socket = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen=resolve; socket.onerror=reject; });
  let seq=0; const pending=new Map(), errors=[], passed=[];
  socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(m.id){const entry=pending.get(m.id);pending.delete(m.id);if(entry)m.error?entry.reject(m.error):entry.resolve(m.result);}};
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const run=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  const check=async(label,expression)=>{assert.equal(await run(expression),true,label);passed.push(label);};
  const click=id=>run(`document.getElementById(${JSON.stringify(id)}).click()`);
  try {
    await send('Runtime.enable'); await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
    await send('Page.navigate',{url:'http://127.0.0.1:8765'});await delay(600);
    await run(`localStorage.removeItem('shiyin-html-prototype-v1')`);await send('Page.reload');await delay(400);
    await check('Initial Chinese creation view',`!document.getElementById('view-create').hidden && document.querySelectorAll('.key').length===13`);
    await click('record');
    await send('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyA',key:'a'});await delay(120);
    await send('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyA',key:'a'});await click('record');
    await check('Keyboard recording captures a note',`document.getElementById('record-info').textContent.includes('1 个音符')`);
    await run(`document.getElementById('theme').focus()`);
    await send('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyS',key:'s'});
    await check('Typing input does not trigger piano',`document.querySelectorAll('.key.pressed').length===0`);
    await run(`(()=>{const dt=new DataTransfer();dt.items.add(new File(['第一行歌词\\n第二行歌词'], 'test.txt', {type:'text/plain'}));document.getElementById('lyrics-file').files=dt.files;document.getElementById('lyrics-file').dispatchEvent(new Event('change'));})()`);await delay(100);
    await check('TXT upload reads local text',`document.getElementById('lyrics').value.includes('第一行歌词') && document.querySelectorAll('#lyric-lines input').length===2`);
    await click('sample-lyrics');
    await check('Lyrics sample has selected segment',`document.querySelectorAll('#lyric-lines input:checked').length===2`);
    await run(`document.querySelectorAll('#lyric-lines input')[3].click()`);
    await check('Nonadjacent selection fills continuous segment',`document.querySelectorAll('#lyric-lines input:checked').length===4`);
    await run(`document.getElementById('demo-result').value='failure'`);await click('generate');await delay(2850);
    await check('Failure preserves existing composition',`document.getElementById('generation-status').textContent.includes('模拟失败') && current().revision===1`);
    await run(`document.getElementById('demo-result').value='success'`);await click('generate');await click('cancel-generation');
    await check('Generation can be cancelled',`document.getElementById('generation-status').textContent.includes('已取消') && current().revision===1`);
    await click('generate');await delay(2850);
    await check('Success opens four-track sample editor',`!document.getElementById('view-edit').hidden && document.querySelectorAll('.track').length===4`);
    await run(`document.querySelector('#roll rect').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
    await run(`document.getElementById('note-start').value='0.5'`);await click('apply-note');
    await check('Note moves and undo restores it',`current().tracks[0].notes[0].start===0.5`);await click('undo');
    await check('Undo restores note position',`current().tracks[0].notes[0].start===0`);await click('redo');
    await check('Redo restores edit',`current().tracks[0].notes[0].start===0.5`);
    await run(`(()=>{const s=document.getElementById('roll'),r=s.getBoundingClientRect();s.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:r.left+r.width*.9,clientY:r.top+70}));})()`);
    await check('Grid adds an editable note',`document.querySelectorAll('#roll rect').length===9`);await click('delete-note');
    await check('Selected note can be deleted',`document.querySelectorAll('#roll rect').length===8`);
    await run(`document.querySelector('.track-controls button').click()`);
    await check('Solo toggles track state',`document.querySelector('.track-controls button').getAttribute('aria-pressed')==='true'`);
    await run(`document.querySelectorAll('.track-controls button')[1].click()`);
    await check('Mute toggles track state',`document.querySelectorAll('.track-controls button')[1].getAttribute('aria-pressed')==='true'`);
    await run(`document.querySelector('.track-controls button').click();document.querySelectorAll('.track-controls button')[1].click()`);
    await click('play');await delay(120);await check('Playback starts',`document.getElementById('play').textContent.includes('停止')`);await click('stop');
    await run(`document.querySelector('[data-view="review"]').click()`);await click('approve');
    await check('Review requires checklist',`document.getElementById('full-song').disabled`);
    await run(`document.querySelectorAll('.review-check').forEach(c=>c.click())`);await click('approve');
    await check('Approval enables explicit next-step only',`!document.getElementById('full-song').disabled && !generating`);
    await run(`document.querySelector('[data-view="edit"]').click();document.getElementById('instruction').value='整首更轻快'`);await click('modify');
    await check('New revision invalidates approval',`current().approved===null && document.getElementById('full-song').disabled`);
    await run(`document.querySelector('[data-view="review"]').click();document.querySelector('[data-export="MP3"]').click()`);
    await check('MP3 explicitly remains a demonstration',`document.getElementById('modal-body').textContent.includes('没有生成或下载 MP3')`);await click('close-modal');
    await click('save');await send('Page.reload');await delay(400);
    await check('Browser save restores lyrics',`document.querySelectorAll('#lyric-lines input:checked').length===4`);
    await click('new-project');await run(`document.querySelector('#modal input').value='另一首歌';document.querySelector('#modal form').requestSubmit()`);
    await check('New project inputs are isolated',`document.getElementById('theme').value==='' && document.getElementById('lyrics').value===''`);
    await run(`document.querySelector('.project-item').click()`);
    await check('Switch restores original project',`document.getElementById('lyrics').value.includes('把昨天装进行囊')`);
    await check('Desktop does not overflow horizontally',`document.documentElement.scrollWidth<=window.innerWidth`);
    const output=process.argv[2];
    if(output){fs.mkdirSync(output,{recursive:true});const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,'prototype-desktop.png'),Buffer.from(shot.data,'base64'));for(const view of ['edit','review']){await run(`show('${view}')`);await delay(150);const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'prototype-'+view+'.png'),Buffer.from(shot.data,'base64'));}await run(`show('create')`);}
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await delay(200);
    await check('Mobile does not overflow horizontally',`document.documentElement.scrollWidth<=window.innerWidth`);
    if(output){const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'prototype-mobile.png'),Buffer.from(shot.data,'base64'));}
    assert.deepEqual(errors,[],'No uncaught browser errors');passed.push('No uncaught browser errors');
    console.log(JSON.stringify({passed:passed.length,checks:passed},null,2));
    if(output)fs.writeFileSync(path.join(output,'prototype-checks.json'),JSON.stringify({passed,errors},null,2));
    await run(`localStorage.removeItem('shiyin-html-prototype-v1')`);
    const fileUrl=require('node:url').pathToFileURL(path.resolve(__dirname,'../frontend/prototype/index.html')).href;
    await send('Page.navigate',{url:fileUrl});await delay(400);
    await check('Direct HTML file opening works',`document.querySelectorAll('.key').length===13 && !document.getElementById('view-create').hidden`);
    console.log('PASS: direct HTML file opening');
  } finally { socket.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
