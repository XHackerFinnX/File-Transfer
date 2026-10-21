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

let activeChoiceMenu=null;
function choiceMenu(trigger,menu,thumbnail,onChoose,onPreview){
  let options=[],selected=null;
  const close=(focus=false)=>{
    menu.hidden=true;trigger.setAttribute('aria-expanded','false');
    if(activeChoiceMenu===api)activeChoiceMenu=null;
    if(focus)trigger.focus({preventScroll:true});
  };
  const open=()=>{
    if(activeChoiceMenu&&activeChoiceMenu!==api)activeChoiceMenu.close();
    activeChoiceMenu=api;menu.hidden=false;trigger.setAttribute('aria-expanded','true');
    menu.firstElementChild?.scrollIntoView({block:'nearest'});
  };
  function setValue(id){
    const option=options.find(item=>item.id===id);if(!option)return;
    selected=id;trigger.value=id;
    trigger.querySelector('.appearance-selected-name').textContent=option.label;
    const current=trigger.querySelector('.appearance-current-thumbnail');
    current.classList.remove('notebook-sample');current.replaceChildren(thumbnail(option));
    for(const row of menu.children){
      const chosen=row.dataset.id===id;row.dataset.selected=String(chosen);
      row.querySelector('.appearance-choice-name').setAttribute('aria-pressed',String(chosen));
    }
  }
  function setOptions(next){
    options=next;menu.replaceChildren();
    for(const option of options){
      const row=document.createElement('div');row.className='appearance-choice';row.dataset.id=option.id;
      const preview=document.createElement('button');preview.type='button';preview.className='appearance-thumbnail-button';
      preview.setAttribute('aria-label','Предпросмотр: '+option.label);preview.setAttribute('aria-haspopup','dialog');
      preview.append(thumbnail(option));preview.addEventListener('click',()=>onPreview(option,preview));
      const name=document.createElement('button');name.type='button';name.className='appearance-choice-name';
      name.dataset.choice=option.id;name.textContent=option.label;
      name.addEventListener('click',()=>{close(true);onChoose(option.id);});
      row.append(preview,name);menu.append(row);
    }
    setValue(selected||trigger.value);
  }
  trigger.addEventListener('click',()=>menu.hidden?open():close());
  trigger.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();open();menu.querySelector('.appearance-choice-name')?.focus();}
    if(event.key==='Escape'&&!menu.hidden){event.preventDefault();event.stopPropagation();close(true);}
  });
  menu.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:index+(event.key==='ArrowDown'?1:-1);
    event.preventDefault();buttons[(next+buttons.length)%buttons.length]?.focus();
  });
  document.addEventListener('pointerdown',event=>{
    if(!menu.hidden&&!document.querySelector('dialog[open]')&&!menu.contains(event.target)&&!trigger.contains(event.target))close();
  });
  const api={setOptions,setValue,close};return api;
}

export function createAppearance(bridge,chooseColor){
  let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
  const prefs={color:validColor(saved.color)?saved.color:null,
    customColor:typeof saved.customColor==='string'&&/^#[0-9a-f]{6}$/i.test(saved.customColor)?saved.customColor:'#663399',
    background:typeof saved.background==='string'?saved.background:'paper',showStats:saved.showStats!==false,
    ballDesign:VolleyBalls.designs.some(design=>design.id===saved.ballDesign)?saved.ballDesign:'classic'};
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

  const note=$('backgroundStatus'),previewDialog=$('appearancePreviewDialog'),previewContent=$('appearancePreviewContent');
  const previewNote=$('appearancePreviewStatus');
  let previewGeneration=0,previewReturn=null;
  function preview(option,source,kind){
    const current=++previewGeneration;previewReturn=source;
    $('appearancePreviewTitle').textContent=option.label;previewNote.textContent='';previewContent.replaceChildren();
    if(kind==='ball'){
      const canvas=document.createElement('canvas');canvas.width=canvas.height=400;canvas.className='appearance-preview-ball';
      canvas.setAttribute('aria-label',option.label);VolleyBalls.render(canvas,option.id);previewContent.append(canvas);
    }else if(option.id==='paper'){
      const paper=document.createElement('div');paper.className='notebook-sample appearance-preview-paper';previewContent.append(paper);
    }else{
      const image=new Image();image.alt=option.label;image.decoding='async';previewNote.textContent='Загружаем предпросмотр…';
      image.onload=()=>{if(current===previewGeneration)previewNote.textContent='';};
      image.onerror=()=>{if(current===previewGeneration)previewNote.textContent='Не удалось загрузить изображение. Закройте предпросмотр и попробуйте ещё раз.';};
      image.src='./assets/'+encodeURIComponent(option.file);previewContent.append(image);
    }
    if(!previewDialog.open)previewDialog.showModal();$('closeAppearancePreview').focus();
  }
  $('closeAppearancePreview').addEventListener('click',()=>previewDialog.close());
  previewDialog.addEventListener('close',()=>{
    previewGeneration++;previewContent.replaceChildren();previewNote.textContent='';
    if(previewReturn?.getClientRects().length)previewReturn.focus({preventScroll:true});
  });
  previewDialog.addEventListener('click',event=>{
    const box=previewDialog.getBoundingClientRect();
    if(event.target===previewDialog&&(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom))previewDialog.close();
  });
  function backgroundThumbnail(option){
    if(option.id==='paper'){const paper=document.createElement('span');paper.className='notebook-sample';return paper;}
    const image=new Image();image.alt='';image.loading='lazy';image.decoding='async';
    image.src=option.thumbnail?'./'+option.thumbnail:'./assets/'+encodeURIComponent(option.file);return image;
  }
  const backgroundMenu=choiceMenu($('backgroundSelect'),$('backgroundMenu'),backgroundThumbnail,setBackground,
    (option,source)=>preview(option,source,'background'));
  backgroundMenu.setOptions([{id:'paper',label:'Обычная тетрадь'}]);
  let generation=0,catalog=[];
  async function setBackground(id){
    const current=++generation,entry=catalog.find(item=>item.id===id);
    if(id!=='paper'&&!entry)return;
    note.textContent='Загружаем фон…';
    let image=null;
    try{
      if(entry){image=new Image();image.decoding='async';image.src='./assets/'+encodeURIComponent(entry.file);await image.decode();}
    }catch{
      if(current===generation)note.textContent='Не удалось загрузить фон. Выберите его ещё раз.';
      return;
    }
    if(current!==generation)return;
    bridge.background(image);prefs.background=id;backgroundMenu.setValue(id);
    note.textContent=store()?'Картинка — предпросмотр, название — выбор. Фон сохранён только у вас.':'Фон виден только вам. Браузер не разрешил сохранить выбор после закрытия страницы.';
  }
  fetch('./backgrounds.json').then(response=>{if(!response.ok)throw Error();return response.json();}).then(entries=>{
    catalog=entries;
    backgroundMenu.setOptions([{id:'paper',label:'Обычная тетрадь'},...catalog]);
    return setBackground(catalog.some(entry=>entry.id===prefs.background)?prefs.background:'paper');
  }).catch(()=>{note.textContent='Список фонов недоступен. Обновите страницу, чтобы попробовать ещё раз.';});
  function ballThumbnail(option){
    const canvas=document.createElement('canvas');canvas.width=canvas.height=96;canvas.setAttribute('aria-hidden','true');
    VolleyBalls.render(canvas,option.id);return canvas;
  }
  function chooseBall(id){
    bridge.ballDesign(id);prefs.ballDesign=id;ballMenu.setValue(id);
    $('ballStatus').textContent=store()?'Дизайн сохранён только у вас. Размер и физика мяча не меняются.':'Дизайн выбран. Браузер не разрешил сохранить его после закрытия страницы.';
  }
  const ballMenu=choiceMenu($('ballSelect'),$('ballMenu'),ballThumbnail,chooseBall,
    (option,source)=>preview(option,source,'ball'));
  ballMenu.setOptions(VolleyBalls.designs);ballMenu.setValue(prefs.ballDesign);bridge.ballDesign(prefs.ballDesign);
  return {preferredColor:fallback=>validColor(prefs.color)?prefs.color:fallback,syncColor};
}
