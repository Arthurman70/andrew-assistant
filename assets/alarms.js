/* Private alarm previews and browser playback; no microphone access. */
(()=>{
 const pi=location.port==='8770',browser=!!window.ANDREW_BROWSER,source=browser?'browser':pi?'pi':'pc';
 const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
 const button=(text,fn)=>{const n=node('button',text);n.type='button';n.onclick=fn;return n};
 const panel=node('section');panel.className='panel';panel.dataset.page='settings';panel.id='alarmSoundPanel';panel.append(node('h2','Alarm sound'));
 const hint=node('p','Choose a sound, Save sound, then Preview. Alarms repeat until dismissed or snoozed, using Andrew’s volume on the requesting device.');hint.className='support-note';panel.append(hint);
 const select=node('select');select.setAttribute('aria-label','Alarm sound');const line=node('div');line.className='feature-form';line.append(select);panel.append(line);
 const message=node('p');message.setAttribute('role','status');panel.append(message);
 const player=new Audio();let revision='',currentKey='',mutedKey='',nextAt=0,quietUntil=0,pending=false,latest=null,loaded=false;
 function volume(){return Math.max(0,Math.min(1,Number(browser?localStorage.getItem('andrew-browser-volume')||80:latest?.pc_volume??80)/100))}
 function stop(){player.pause();player.currentTime=0}
 async function api(path,data){if(!pi)return window.api(path,data);const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Andrew-Screen':'1'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'Alarm sound could not be changed.');return j}
 async function preview(){try{const r=await api('/api/alarm-preview',{source});message.textContent=r.answer;if(browser){stop();player.src='/api/alarm-sound';player.volume=volume();await player.play()}}catch(e){message.textContent=e.message}}
 const enable=button('Enable alarm sound in this browser',()=>{mutedKey='';quietUntil=0;nextAt=0;playAlarm(true)});enable.hidden=true;
 const banner=node('div','An alarm is ringing. ');banner.hidden=true;banner.setAttribute('role','alert');Object.assign(banner.style,{position:'fixed',bottom:'16px',right:'16px',zIndex:10,padding:'16px',background:'#183e35',border:'1px solid #99ecd4',borderRadius:'12px',maxWidth:'90vw'});banner.append(button('Enable alarm sound',()=>{mutedKey='';quietUntil=0;nextAt=0;playAlarm(true)}));if(browser)document.body.append(banner);
 line.append(button('Save sound',async()=>{try{const r=await api('/api/alarm-sound',{sound:select.value});message.textContent=r.answer;await window.refresh()}catch(e){message.textContent=e.message}}),button('Preview',preview),enable);
 if(pi)document.querySelector('footer').before(panel);else{const grid=document.querySelector('.grid');grid.append(panel);panel.hidden=grid.dataset.page!=='settings'}
 async function load(){if(loaded)return;loaded=true;try{const r=await api('/api/alarm-sounds');for(const sound of r.sounds)select.add(new Option(sound.label,sound.id));select.value=r.selected}catch(e){loaded=false;message.textContent=e.message}}
 async function playAlarm(gesture=false){
  const talk=document.getElementById('browserTalk');if(!browser||!currentKey||currentKey===mutedKey||pending||!player.paused||Date.now()<quietUntil||Date.now()<nextAt||talk?.disabled||talk?.getAttribute('aria-pressed')==='true'||(!gesture&&document.getElementById('mascot')?.classList.contains('speaking')))return;
  pending=true;const key=currentKey;try{player.volume=volume();await player.play();if(currentKey!==key)stop();else{enable.hidden=true;banner.hidden=true;nextAt=Date.now()+8000}}catch{enable.hidden=false;banner.hidden=false;message.textContent='Tap Enable alarm sound once. Keep this page open for browser alarms.'}finally{pending=false}
 }
 window.addEventListener('andrew-input',()=>{quietUntil=Date.now()+6000;stop()});
 window.addEventListener('andrew-speech-control',e=>{if(e.detail==='resume'){mutedKey='';nextAt=0}else{mutedKey=currentKey;stop()}});
 const previous=window.renderUpgrade;
 window.renderUpgrade=state=>{previous?.(state);latest=state;load();const audio=state.alarm_audio;if(!audio)return;if(document.activeElement!==select&&[...select.options].some(o=>o.value===audio.selected))select.value=audio.selected;if(audio.error)message.textContent=audio.error;
  if(browser){const active=(audio.devices?.browser?.alarms||[]).filter(a=>!a.device||a.device===window.ANDREW_DEVICE_ID);const key=active.map(a=>a.token).sort().join(':');if(key!==currentKey||revision!==audio.revision){stop();currentKey=key;revision=audio.revision;nextAt=0;if(key)player.src='/api/alarm-sound';else{enable.hidden=true;banner.hidden=true}}playAlarm()}
  if(pi)panel.hidden=document.body.classList.contains('game-active');
 };
 window.addEventListener('pagehide',stop);setInterval(()=>playAlarm(),500);
})();
