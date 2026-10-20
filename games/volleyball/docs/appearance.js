const key='volley.appearance.v1';
const validColor=color=>Number.isInteger(color)&&color>=0&&color<5||typeof color==='string'&&/^#[0-9a-f]{6}$/i.test(color);
const $=id=>document.getElementById(id);
const clamp=value=>Math.max(0,Math.min(1,value));
function fromHex(hex,previousHue=0){
  const [r,g,b]=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255);
  const max=Math.max(r,g,b),min=Math.min(r,g,b),delta=max-min;
  let h=previousHue;
  if(delta)h=((max===r?(g-b)/delta:max===g?(b-r)/delta+2:(r-g)/delta+4)*60+360)%360;
  return {h,s:max?delta/max:0,v:max};
}
function toHex({h,s,v}){
  const c=v*s,x=c*(1-Math.abs(h/60%2-1)),m=v-c;
  const rgb=h<60?[c,x,0]:h<120?[x,c,0]:h<180?[0,c,x]:h<240?[0,x,c]:h<300?[x,0,c]:[c,0,x];
  return '#'+rgb.map(value=>Math.round((value+m)*255).toString(16).padStart(2,'0')).join('');
}

export function createAppearance(bridge,chooseColor){
  let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
  const prefs={color:validColor(saved.color)?saved.color:null,
    customColor:typeof saved.customColor==='string'&&/^#[0-9a-f]{6}$/i.test(saved.customColor)?saved.customColor:'#663399',
    background:typeof saved.background==='string'?saved.background:'paper',showStats:saved.showStats!==false};
  const store=()=>{try{localStorage.setItem(key,JSON.stringify(prefs));return true;}catch{return false;}};
  const button=$('customColorButton'),dialog=$('customColorDialog'),picker=$('customColorPicker'),hex=$('customColorHex');
  const panel=$('colorPalettePanel'),palette=$('colorPalette'),marker=$('colorPaletteMarker'),hue=$('customColorHue');
  let hsv=fromHex(prefs.customColor),palettePointer=null;
  function paint(writeHex=true){
    const color=toHex(hsv);
    picker.value=color;picker.style.setProperty('--picked-color',color);
    if(writeHex)hex.value=color;
    hue.value=Math.round(hsv.h)%360;
    palette.style.setProperty('--palette-hue',toHex({h:hsv.h,s:1,v:1}));
    marker.style.left=hsv.s*100+'%';marker.style.top=(1-hsv.v)*100+'%';
    palette.setAttribute('aria-valuenow',String(Math.round(hsv.s*100)));
    palette.setAttribute('aria-valuetext',`Насыщенность ${Math.round(hsv.s*100)}%, яркость ${Math.round(hsv.v*100)}%. ${color}`);
  }
  function expandPalette(expanded){
    panel.hidden=!expanded;picker.setAttribute('aria-expanded',String(expanded));
    dialog.classList.toggle('palette-open',expanded);
    picker.querySelector('.color-picker-label').textContent=expanded?'СВЕРНУТЬ ПАЛИТРУ':'ОТКРЫТЬ ПАЛИТРУ';
  }
  picker.addEventListener('click',()=>{expandPalette(panel.hidden);if(!panel.hidden)palette.focus({preventScroll:true});});
  hue.addEventListener('input',()=>{hsv.h=Number(hue.value);paint();});
  function pointColor(event){
    const box=palette.getBoundingClientRect();
    hsv.s=clamp((event.clientX-box.left)/box.width);hsv.v=1-clamp((event.clientY-box.top)/box.height);paint();
  }
  palette.addEventListener('pointerdown',event=>{
    if(event.pointerType==='mouse'&&event.button!==0||palettePointer!==null)return;
    event.preventDefault();palettePointer=event.pointerId;palette.setPointerCapture(event.pointerId);
    palette.focus({preventScroll:true});pointColor(event);
  });
  palette.addEventListener('pointermove',event=>{if(event.pointerId===palettePointer){event.preventDefault();pointColor(event);}});
  for(const type of ['pointerup','pointercancel','lostpointercapture'])palette.addEventListener(type,event=>{
    if(event.pointerId!==palettePointer)return;
    palettePointer=null;if(palette.hasPointerCapture(event.pointerId))palette.releasePointerCapture(event.pointerId);
  });
  palette.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    event.preventDefault();const step=event.shiftKey ? .1 : .01;
    if(event.key==='ArrowLeft')hsv.s=clamp(hsv.s-step);
    if(event.key==='ArrowRight')hsv.s=clamp(hsv.s+step);
    if(event.key==='ArrowUp')hsv.v=clamp(hsv.v+step);
    if(event.key==='ArrowDown')hsv.v=clamp(hsv.v-step);
    if(event.key==='Home')hsv.s=0;
    if(event.key==='End')hsv.s=1;
    paint();
  });
  function syncColor(color){
    if(!validColor(color))return;
    const custom=typeof color==='string';
    const changed=prefs.color!==color||custom&&prefs.customColor!==color;
    prefs.color=color;
    if(custom)prefs.customColor=color;
    if(changed)store();
    button.setAttribute('aria-pressed',String(custom));
    button.style.setProperty('--custom-color',prefs.customColor);
  }
  button.addEventListener('click',()=>{
    hsv=fromHex(prefs.customColor);paint();expandPalette(false);$('customColorError').textContent='';
    dialog.showModal();picker.focus();
  });
  hex.addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test(hex.value)){hsv=fromHex(hex.value,hsv.h);paint(false);}});
  $('cancelCustomColor').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>button.focus());
  $('customColorForm').addEventListener('submit',event=>{
    event.preventDefault();
    const color=hex.value.toLowerCase();
    if(!/^#[0-9a-f]{6}$/.test(color))return;
    if(!chooseColor(color)){$('customColorError').textContent='Нет связи с сервером. Попробуйте ещё раз.';return;}
    prefs.color=prefs.customColor=color;syncColor(color);
    $('colorStatus').textContent=store()?'Цвет сохранён.':'Цвет выбран. Браузер не разрешил сохранить его после закрытия страницы.';
    dialog.close();
  });
  function showStats(visible){
    document.body.dataset.stats=visible?'visible':'hidden';
    $('toggleStatsButton').textContent=visible?'СКРЫТЬ ПИНГ И FPS':'ПОКАЗАТЬ ПИНГ И FPS';
    $('toggleStatsButton').setAttribute('aria-pressed',String(visible));
  }
  showStats(prefs.showStats);
  $('toggleStatsButton').addEventListener('click',()=>{prefs.showStats=!prefs.showStats;showStats(prefs.showStats);store();});

  const select=$('backgroundSelect'),note=$('backgroundStatus');
  let generation=0,selected='paper',catalog=[];
  async function setBackground(id){
    const current=++generation,entry=catalog.find(item=>item.id===id);
    if(id!=='paper'&&!entry)return;
    note.textContent='Загружаем фон…';
    let image=null;
    try{
      if(entry){image=new Image();image.decoding='async';image.src='./assets/'+encodeURIComponent(entry.file);await image.decode();}
    }catch{
      if(current===generation){select.value=selected;note.textContent='Не удалось загрузить фон. Выберите его ещё раз.';}
      return;
    }
    if(current!==generation)return;
    bridge.background(image);selected=id;prefs.background=id;select.value=id;
    note.textContent=store()?'Фон виден только вам и сохранён в этом браузере.':'Фон виден только вам. Браузер не разрешил сохранить выбор после закрытия страницы.';
  }
  select.addEventListener('change',()=>setBackground(select.value));
  fetch('./backgrounds.json').then(response=>{if(!response.ok)throw Error();return response.json();}).then(entries=>{
    catalog=entries;
    for(const entry of catalog){const option=document.createElement('option');option.value=entry.id;option.textContent=entry.label;select.append(option);}
    return setBackground(catalog.some(entry=>entry.id===prefs.background)?prefs.background:'paper');
  }).catch(()=>{note.textContent='Список фонов недоступен. Обновите страницу, чтобы попробовать ещё раз.';});
  return {preferredColor:fallback=>validColor(prefs.color)?prefs.color:fallback,syncColor};
}
