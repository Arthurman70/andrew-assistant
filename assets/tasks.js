/* Scheduled commands use typed API arguments; task/model text is never HTML. */
(()=>{
 const pi=location.port==='8770',source=window.ANDREW_BROWSER?'browser':pi?'pi':'pc';
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
 const button=(text,fn)=>{const b=el('button',text);b.type='button';b.onclick=fn;return b};
 const field=label=>{const i=el('input');i.placeholder=label;i.setAttribute('aria-label',label);return i};
 const panel=el('section',undefined,'panel tasks-panel');panel.id='taskPanel';panel.dataset.page='home';panel.append(el('h2','Scheduled tasks'),el('p','Andrew runs these tasks at the scheduled time. Timers and alarms only alert. Times follow the host PC’s clock; the PC must be awake.','support-note'));
 const form=el('form',undefined,'task-create'),name=field('Task name (optional)'),command=field('Task to run, e.g. open Chrome'),when=field('When, e.g. tomorrow at 7 pm');name.maxLength=80;command.maxLength=pi?6000:32000;when.maxLength=160;command.required=when.required=true;
 const create=button('Schedule task',()=>form.requestSubmit());form.append(name,command,when,create);panel.append(form);
 const notice=el('p','Say “open Chrome in ten minutes” or “read the news at 7 am every weekday”.','support-note');notice.setAttribute('role','status');panel.append(notice);
 const list=el('div',undefined,'task-list');panel.append(list);
 if(pi)document.querySelector('footer').before(panel);else{const grid=document.querySelector('.grid');grid.append(panel);panel.hidden=!!grid.dataset.page&&grid.dataset.page!=='home'}
 let latest=[],signature='',busy=false,lastNavigation='';
 const status={scheduled:'Scheduled',queued:'Queued · waiting for PC or another task',running:'Running',complete:'Finished',failed:'Needs retry',needs_attention:'Needs attention',paused:'Paused',cancelled:'Cancelled'};
 async function action(data){if(busy)return;busy=true;create.disabled=true;try{let r;if(pi){const response=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json','X-Andrew-Screen':'1'},body:JSON.stringify({...data,source})});r=await response.json();if(!response.ok)throw Error(r.error||'The task could not be changed.')}else r=await window.api('/api/tasks',{...data,source});notice.textContent=r.answer;await window.refresh()}catch(e){notice.textContent=e.message||'The task could not be changed.'}finally{busy=false;create.disabled=false}}
 form.onsubmit=async e=>{e.preventDefault();await action({action:'create',name:name.value.trim(),command:command.value.trim(),when:when.value.trim()})};
 function draw(){
  if(list.contains(document.activeElement)&&['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))return false;
  list.replaceChildren();if(!latest.length){list.append(el('p','No tasks scheduled yet.','empty-state'));return true}
  for(const task of latest){
   const card=el('article',undefined,'task-card');card.dataset.taskId=task.id;
   const heading=el('div',undefined,'task-heading');heading.append(el('strong',task.name==='Task '+task.number?task.name:'Task '+task.number+' · '+task.name),el('span',status[task.status]||task.status,'task-status'));card.append(heading);
   card.append(el('p',task.command,'task-command'),el('p',task.due_display+' · '+task.repeat_label,'task-time'),el('small',({'pc':'PC','pi':'Pi','browser':'Browser'})[task.source]+' · '+(task.person_name||'Guest')+' · '+task.provider+' / '+(task.model||'account default')));
   if(task.result)card.append(el('p',task.result,'task-result'));
   const controls=el('div',undefined,'schedule-actions');const target=task.id;
   if(task.status!=='running')controls.append(button(['scheduled','queued','paused'].includes(task.status)?'Run now':'Run again',()=>action({action:'run',target})));
   if(task.status==='scheduled'||task.status==='queued')controls.append(button('Pause',()=>action({action:'pause',target})));
   if(task.status==='paused')controls.append(button('Resume',()=>action({action:'resume',target})));
   if(['scheduled','queued','paused','running','needs_attention'].includes(task.status))controls.append(button('Cancel',()=>action({action:'cancel',target})));
   card.append(controls);
   if(task.status!=='running'){
    const edit=el('details');edit.append(el('summary','Change run time'));const f=el('form',undefined,'schedule-edit'),time=field('New time for task '+task.number);time.required=true;time.maxLength=160;f.append(time,button('Save time',()=>f.requestSubmit()));f.onsubmit=e=>{e.preventDefault();action({action:'time',target,when:time.value.trim()})};edit.append(f);card.append(edit);
   }
   list.append(card);
  }return true;
 }
 const original=window.renderUpgrade;
 window.renderUpgrade=state=>{original?.(state);latest=state.scheduled_tasks||[];const next=JSON.stringify(latest);if(next!==signature&&draw())signature=next;if(state.task_scheduler?.state==='retrying')notice.textContent=state.task_scheduler.detail;if(pi){panel.hidden=document.body.classList.contains('game-active');const nav=state.navigation?.pi;if(nav&&nav.id!==lastNavigation&&Date.now()/1000-nav.at<20){lastNavigation=nav.id;if(nav.section==='taskPanel')panel.scrollIntoView({behavior:'smooth',block:'start'})}}};
})();
