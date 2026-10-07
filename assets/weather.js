/* Forecasts are real provider data. Never insert city/model text as HTML. */
(()=>{
 const pi=location.port==='8770',source=window.ANDREW_BROWSER?'browser':pi?'pi':'pc';
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
 const button=(text,fn)=>{const n=el('button',text);n.type='button';n.onclick=fn;return n};
 const panel=el('section',undefined,'panel weather-panel');panel.id='weatherPanel';panel.dataset.page='home';
 panel.append(el('h2','Weather'));
 const form=el('form',undefined,'weather-controls');const city=el('input');city.placeholder='City, state or country';city.setAttribute('aria-label','Weather location');city.maxLength=160;
 const period=el('select');period.setAttribute('aria-label','Forecast period');for(const [name,value] of [['Today','today'],['Tomorrow','tomorrow'],['Next 3 days','next three days'],['Next week','next week'],['Next 2 weeks','next two weeks'],['Next 6 hours','next six hours'],['Next 24 hours','next 24 hours']])period.add(new Option(name,value));period.value='next three days';
 const detail=el('select');detail.setAttribute('aria-label','Forecast detail');detail.add(new Option('Daily','daily'));detail.add(new Option('Hourly','hourly'));
 const units=el('select');units.setAttribute('aria-label','Temperature units');units.add(new Option('°F','fahrenheit'));units.add(new Option('°C','celsius'));
 const submit=button('Show forecast',()=>form.requestSubmit());form.append(city,period,detail,units,submit);panel.append(form);
 const message=el('p','Ask “weather in Boston for the next three days” or “hourly weather for tomorrow”.','weather-hint');message.setAttribute('role','status');panel.append(message);
 const content=el('div');panel.append(content);
 const attribution=el('a','Forecast data: Open-Meteo','weather-credit');attribution.href='https://open-meteo.com/';attribution.target='_blank';attribution.rel='noopener';panel.append(attribution);
 if(pi)document.querySelector('footer').before(panel);else{const grid=document.querySelector('.grid');grid.append(panel);panel.hidden=!!grid.dataset.page&&grid.dataset.page!=='home';}
 let latest=null,key='',selectedDay='',mode='daily',lastNavigation='';
 const temp=value=>value===null||value===undefined?'—':Math.round(value)+'°';
 const value=(n,unit)=>n===null||n===undefined?'—':Math.round(n)+(unit||'');
 const dateLabel=(date,opts)=>new Intl.DateTimeFormat(undefined,{timeZone:'UTC',...opts}).format(new Date(date+'T12:00:00Z'));
 const timeLabel=time=>new Intl.DateTimeFormat(undefined,{timeZone:'UTC',hour:'numeric'}).format(new Date(time+'Z'));
 const icon=(glyph,label)=>{const n=el('span',glyph,'weather-icon');n.setAttribute('aria-hidden','true');n.title=label;return n};
 form.onsubmit=async event=>{
  event.preventDefault();submit.disabled=true;message.textContent='Getting the forecast…';
  try{
   const location=city.value.trim()||latest?.city||'';
   if(pi)await window.ask('weather'+(location?' in '+location:'')+' for '+period.value+' '+detail.value+' in '+units.value);
   else{const result=await window.api('/api/weather',{city:location,period:period.value,detail:detail.value,units:units.value,source});message.textContent=result.answer;await window.refresh();}
  }catch(error){message.textContent=error.message||'The forecast could not be loaded. Try again.';}finally{submit.disabled=false;}
 };
 function hourly(view,day){
  const section=el('section',undefined,'weather-hourly');section.append(el('h3','Hourly forecast'));
  const picker=el('select');picker.setAttribute('aria-label','Hourly forecast day');
  picker.add(new Option('All requested hours','all'));for(const d of view.days)picker.add(new Option(d.label+' · '+dateLabel(d.date,{month:'short',day:'numeric'}),d.date));
  picker.value=day||'all';picker.onchange=()=>{selectedDay=picker.value;draw()};section.append(picker);
  const hours=view.hours.filter(h=>!day||day==='all'||h.date===day);const visible=mode==='hourly'?hours:hours.slice(0,12);
  const strip=el('div',undefined,'weather-hour-strip');strip.setAttribute('tabindex','0');strip.setAttribute('aria-label','Hourly forecast, scroll for more hours');
  for(const h of visible){const card=el('div',undefined,'weather-hour');card.append(el('small',dateLabel(h.date,{weekday:'short'})+' '+timeLabel(h.time)),icon(h.icon,h.condition),el('strong',temp(h.temperature)),el('span',h.condition),el('small','Feels '+temp(h.feels_like)),el('small','💧 '+value(h.rain_chance,'%')),el('small','Wind '+value(h.wind,' '+view.wind_unit)));strip.append(card)}
  section.append(strip);
  if(mode!=='hourly'&&hours.length>12)section.append(button('Show all '+hours.length+' hourly readings',()=>{mode='hourly';draw()}));
  if(mode==='hourly'){
   const wrap=el('div',undefined,'weather-hour-table');const table=el('table');table.append(el('caption','Hourly readings · '+view.timezone));const head=el('thead');const tr=el('tr');for(const text of ['Time','Forecast','Temp','Feels like','Precipitation','Wind'])tr.append(el('th',text));head.append(tr);table.append(head);const body=el('tbody');
   for(const h of hours){const row=el('tr');for(const text of [dateLabel(h.date,{weekday:'short'})+' '+timeLabel(h.time),h.condition,temp(h.temperature),temp(h.feels_like),value(h.rain_chance,'%'),value(h.wind,' '+view.wind_unit)])row.append(el('td',text));body.append(row)}table.append(body);wrap.append(table);section.append(wrap);
  }
  return section;
 }
 function draw(){
  const view=latest;if(!view)return;content.replaceChildren();message.textContent=view.error||'';
  const now=el('div',undefined,'weather-now');const heading=el('div');heading.append(el('h3',view.location),el('p',view.period+' · '+view.timezone,'weather-subtitle'));const current=el('div',undefined,'weather-current');current.append(icon(view.current.icon,view.current.condition),el('strong',temp(view.current.temperature),'weather-big-temp'),el('span',view.unit_label));heading.append(current,el('p',view.current.condition+' · Feels like '+temp(view.current.feels_like)));
  const stats=el('div',undefined,'weather-stats');for(const [name,val] of [['Humidity',value(view.current.humidity,'%')],['Wind',value(view.current.wind,' '+view.wind_unit)],['Updated',new Date(view.updated_at*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})]]){const item=el('div');item.append(el('span',name),el('strong',val));stats.append(item)}now.append(heading,stats);content.append(now);
  const tabs=el('div',undefined,'weather-tabs');for(const option of ['daily','hourly']){const b=button(option==='daily'?'Daily forecast':'Hourly forecast',()=>{mode=option;draw()});b.setAttribute('aria-pressed',String(mode===option));tabs.append(b)}content.append(tabs);
  const board=el('div',undefined,'weather-days');const lows=view.days.map(d=>d.low).filter(n=>n!==null),highs=view.days.map(d=>d.high).filter(n=>n!==null);const min=Math.min(...lows),max=Math.max(...highs);
  for(const d of view.days){const card=button('',()=>{selectedDay=d.date;mode='hourly';draw();content.querySelector('.weather-hourly')?.scrollIntoView({behavior:'smooth',block:'nearest'})});card.className='weather-day';card.setAttribute('aria-label',d.label+', '+d.condition+', high '+temp(d.high)+', low '+temp(d.low)+'. Show hourly forecast.');card.append(el('strong',d.label),el('small',dateLabel(d.date,{month:'short',day:'numeric'})),icon(d.icon,d.condition),el('span',d.condition,'weather-condition'));const temperatures=el('div',undefined,'weather-hi-lo');temperatures.append(el('span','High '+temp(d.high),'weather-high'),el('span','Low '+temp(d.low),'weather-low'));card.append(temperatures);const bar=el('div',undefined,'weather-range');const fill=el('span');if(d.low!==null&&d.high!==null&&Number.isFinite(min)&&Number.isFinite(max)){fill.style.marginLeft=((d.low-min)/(max-min||1)*75)+'%';fill.style.width=(Math.max(8,(d.high-d.low)/(max-min||1)*100))+'%'}bar.append(fill);card.append(bar,el('small','💧 '+value(d.rain_chance,'%')+' precip.'));board.append(card)}content.append(board,hourly(view,selectedDay));
 }
 const original=window.renderUpgrade;
 window.renderUpgrade=state=>{
  original?.(state);const view=state.daily?.weather?.[source];if(document.activeElement!==city&&!city.value)city.value=state.daily?.weather_city||'';
  const signature=JSON.stringify(view||null);if(view&&signature!==key){key=signature;latest=view;mode=view.mode;if(!view.days.some(d=>d.date===selectedDay))selectedDay='all';if(document.activeElement!==city)city.value=view.city;units.value=view.units;detail.value=view.mode;const choice={'Today':'today','Tomorrow':'tomorrow','Next 3 days':'next three days','Next 7 days':'next week','Next 14 days':'next two weeks','Next 6 hours':'next six hours','Next 24 hours':'next 24 hours'}[view.period];if(choice&&document.activeElement!==period)period.value=choice;draw()}
  if(pi){panel.hidden=document.body.classList.contains('game-active');const nav=state.navigation?.pi;if(nav&&nav.id!==lastNavigation&&Date.now()/1000-nav.at<20){lastNavigation=nav.id;if(nav.section==='weatherPanel')panel.scrollIntoView({behavior:'smooth',block:'start'});else if(nav.page==='home'&&!nav.section)window.scrollTo({top:0,behavior:'smooth'})}}
 };
})();
