const key='volley.appearance.v1';
const validColor=color=>Number.isInteger(color)&&color>=0&&color<5||typeof color==='string'&&/^#[0-9a-f]{6}$/i.test(color);
const $=id=>document.getElementById(id);

export function createAppearance(bridge,chooseColor){
  let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}
  const prefs={color:validColor(saved.color)?saved.color:null,
    customColor:typeof saved.customColor==='string'&&/^#[0-9a-f]{6}$/i.test(saved.customColor)?saved.customColor:'#663399',
    background:typeof saved.background==='string'?saved.background:'paper'};
  const store=()=>{try{localStorage.setItem(key,JSON.stringify(prefs));return true;}catch{return false;}};
  const button=$('customColorButton'),dialog=$('customColorDialog'),picker=$('customColorPicker'),hex=$('customColorHex');
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
    picker.value=hex.value=prefs.customColor;$('customColorError').textContent='';
    dialog.showModal();hex.focus();
  });
  picker.addEventListener('input',()=>{hex.value=picker.value;});
  hex.addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test(hex.value))picker.value=hex.value;});
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
