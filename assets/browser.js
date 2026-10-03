/* Foreground, explicit push-to-talk only. No browser background transcription. */
window.ANDREW_BROWSER=true;
(()=>{
const originalFetch=window.fetch.bind(window);let context,player,buffer,gain,offset=0,started=0,recording=null,installPrompt,generation=0,paused=false;
let volume=Number(localStorage.getItem('andrew-browser-volume')||80)/100;
function audio(){context ||= new (window.AudioContext||window.webkitAudioContext)();if(context.state==='suspended')context.resume();return context}
function stop(keep=true){if(player){offset+=audio().currentTime-started;player.onended=null;try{player.stop()}catch{}player=null}if(!keep){offset=0;buffer=null}document.getElementById('mascot')?.classList.remove('speaking')}
function play(){if(!buffer||offset>=buffer.duration)return;stop(true);player=audio().createBufferSource();player.buffer=buffer;gain=audio().createGain();gain.gain.value=volume;player.connect(gain).connect(audio().destination);started=audio().currentTime;player.onended=()=>{player=null;offset=0;buffer=null;document.getElementById('mascot')?.classList.remove('speaking')};player.start(0,offset);document.getElementById('mascot')?.classList.add('speaking')}
async function speak(encoded,ticket=generation){stop(false);const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));buffer=await audio().decodeAudioData(bytes.buffer);if(ticket===generation&&!paused)play();else if(!paused)stop(false)}
function localControl(action){generation++;paused=action==='pause';if(action==='pause')stop(true);else if(action==='stop')stop(false);else play();return {answer:action==='resume'?'Continuing.':'Speech paused.',silent:true}}
window.fetch=async (url,options={})=>{
 const path=typeof url==='string'?url:'';options={...options};options.headers=new Headers(options.headers||{});
 if(options.method==='POST')options.headers.set('X-Andrew-CSRF',document.querySelector('meta[name=andrew-csrf]')?.content||'');
 if(path==='/api/speech-control'){const data=JSON.parse(options.body||'{}');return new Response(JSON.stringify(localControl(data.action)),{headers:{'Content-Type':'application/json'}})}
 if(path==='/api/command'){
  const data=JSON.parse(options.body||'{}'),phrase=(data.text||'').toLowerCase().trim().replace(/[.!?]+$/,'');
  if(/^(shut up|stop talking|pause speaking|be quiet|continue|keep going|resume speaking)$/.test(phrase))return new Response(JSON.stringify(localControl(/continue|keep going|resume/.test(phrase)?'resume':'pause')),{headers:{'Content-Type':'application/json'}});
  generation++;paused=false;stop(false);data.volume=volume*100;data.speak=!!document.getElementById('speech')?.checked;options.body=JSON.stringify(data);
 }
 const ticket=generation;const response=await originalFetch(url,options);
 if(response.status===401&&path.startsWith('/api/')){location.reload();return response}
 if(response.ok&&['/api/command','/api/browser-voice','/api/browser-notifications'].includes(path)){
  const data=await response.clone().json();if(data.volume!==undefined){volume=Number(data.volume)/100;localStorage.setItem('andrew-browser-volume',String(data.volume))}if(data.speech_action)localControl(data.speech_action);else if(data.audio&&document.getElementById('speech')?.checked){try{await speak(data.audio,ticket)}catch{window.toast?.('Tap Continue to play the reply.')}}
 }
 return response;
};
async function talk(){
 if(recording){recording.finish();return}
 audio();const button=document.getElementById('browserTalk');
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
  const capture=new AudioContext(),source=capture.createMediaStreamSource(stream),node=capture.createScriptProcessor(4096,1,1),frames=[];
  const mute=capture.createGain();mute.gain.value=0;source.connect(node);node.connect(mute);mute.connect(capture.destination);
  node.onaudioprocess=e=>frames.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  button.textContent='Finish request';button.setAttribute('aria-pressed','true');window.toast?.('Listening to this request. Tap again when finished.');
  let finished=false;
  const finish=async()=>{
   if(finished)return;finished=true;clearTimeout(timer);node.disconnect();source.disconnect();stream.getTracks().forEach(t=>t.stop());await capture.close();recording=null;button.textContent='Talk to Andrew';button.setAttribute('aria-pressed','false');
   const count=frames.reduce((n,f)=>n+f.length,0),all=new Float32Array(count);let at=0;for(const f of frames){all.set(f,at);at+=f.length}const pcm=new Int16Array(Math.floor(count*16000/capture.sampleRate));for(let i=0;i<pcm.length;i++){const x=i*capture.sampleRate/16000,a=Math.floor(x),fraction=x-a;pcm[i]=Math.max(-32768,Math.min(32767,((all[a]||0)*(1-fraction)+(all[a+1]||0)*fraction)*32767))}let bytes='';for(const b of new Uint8Array(pcm.buffer))bytes+=String.fromCharCode(b);
   window.toast?.('Thinking…');try{const r=await fetch('/api/browser-voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pcm:btoa(bytes),activated:true,volume:volume*100,speak:document.getElementById('speech').checked})});const j=await r.json();if(!r.ok)throw Error(j.error);document.getElementById('heard').textContent='You: '+(j.transcript||'');document.getElementById('answer').textContent=j.answer||'';window.toast?.(j.answer||'No speech heard. Try again.')}catch(e){window.failure?.(e)}
  };
  const timer=setTimeout(finish,15000);recording={finish,stream,cancel:()=>{finished=true;clearTimeout(timer);node.disconnect();source.disconnect();stream.getTracks().forEach(t=>t.stop());capture.close();recording=null}};
 }catch(e){button.textContent='Talk to Andrew';window.toast?.('Microphone unavailable. Allow microphone access or type your request.')}
}
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;const b=document.getElementById('browserInstall');if(b)b.hidden=false});
window.addEventListener('DOMContentLoaded',()=>{
 if(!document.getElementById('mascot'))return;
 const row=document.createElement('div');row.className='row';row.innerHTML='<button id="browserTalk" class="primary" aria-pressed="false">Talk to Andrew</button><button id="browserInstall">Install app</button><button id="browserLogout">Sign out</button>';
 document.getElementById('speechControls').after(row);document.getElementById('browserTalk').onclick=talk;
 document.getElementById('browserInstall').onclick=async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null}else window.toast?.('Use your browser’s Install app or Add to Home Screen option.')};
 document.getElementById('browserLogout').onclick=async()=>{stop(false);await fetch('/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});location.reload()};
 const label=document.createElement('label');label.textContent='Andrew volume on this device ';const range=document.createElement('input');range.type='range';range.min=0;range.max=100;range.value=volume*100;range.oninput=()=>{volume=Number(range.value)/100;localStorage.setItem('andrew-browser-volume',range.value);if(gain)gain.gain.value=volume};label.append(range);row.after(label);
 document.querySelectorAll('#talkPc,#talkPi').forEach(b=>{b.hidden=true});document.getElementById('speech').parentElement.lastChild.textContent=' Speak replies on this device';
 const hint=()=>{document.getElementById('hint').textContent='Tap Talk for one request, or type below. The microphone stays closed otherwise.'};hint();setInterval(hint,1000);
 document.addEventListener('pointerdown',()=>audio(),{once:true});
 navigator.serviceWorker?.register('/sw.js');
 setInterval(async()=>{try{const r=await fetch('/api/browser-notifications');if(r.ok){const j=await r.json();if(j.answer){document.getElementById('answer').textContent=j.answer;window.toast?.(j.answer)}}}catch{}},2000);
});
window.addEventListener('pagehide',()=>{recording?.cancel();generation++;stop(false)});
})();
