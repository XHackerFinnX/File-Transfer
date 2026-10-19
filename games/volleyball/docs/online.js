import { COLORS } from './shared/settings.js';
import { PlayerPrediction } from './shared/prediction.js';
const bridge=window.VolleyBridge;
const $=id=>document.getElementById(id);
const names=['Классик','Красный','Синий','Зелёный','Розовый'];
const messages={
  ROOM_UNAVAILABLE:'Комната не найдена, занята или сейчас недоступна.',RATE_LIMIT:'Слишком много попыток. Подождите минуту и попробуйте ещё раз.',
  SERVER_FULL:'Сервер занят. Попробуйте чуть позже.',NOT_HOST:'Это действие доступно только хозяину комнаты.',WAITING_PLAYER:'Дождитесь подключения второго игрока.',
  INVALID_SETTINGS:'Сервер отклонил параметры. Проверьте значения и повторите.',SESSION_EXPIRED:'Время восстановления истекло. Создайте комнату или введите её код заново.',
  HOST_LEFT:'Хозяин покинул комнату.',PLAYER_LEFT:'Вы вышли из комнаты.',KICKED:'Хозяин освободил ваше место.',ROOM_EXPIRED:'Срок жизни комнаты закончился. Создайте новую.',
  ALREADY_IN_ROOM:'Вы уже находитесь в комнате.',INVALID_STATE:'Это действие сейчас недоступно.',INVALID_MESSAGE:'Не удалось выполнить действие.',
  SERVER_ERROR:'На сервере произошла ошибка. Подключитесь ещё раз.',NOT_IN_ROOM:'Сначала войдите в комнату.'
};
let socket=null,connecting=null,room=null,role=null,session=null,serverURL='',busy=false,manualClose=false;
let reconnectTimer=null,retry=0,toastTimer=null,actionTimer=null,networkOK=false;
let seq=0,lastInputAt=0,lastMask=-1,lastStateSeq=-1,lastEvent=0,snapshots=[],rtt=0;
const pressed=new Set(),pingTimes=new Map();
const prediction=new PlayerPrediction();
const storageKey='volley.online.session.v1';
function status(text,error=false){$('serverStatus').textContent=text;$('serverStatus').dataset.error=String(error);}
function toast(text){$('onlineToast').textContent=text;$('onlineToast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('onlineToast').hidden=true,4500);}
function setBusy(value){busy=value;$('createRoomButton').disabled=value||!serverURL;$('joinRoomButton').disabled=value||!serverURL;}
function saveSession(){try{if(session)sessionStorage.setItem(storageKey,JSON.stringify({...session,url:serverURL}));else sessionStorage.removeItem(storageKey);}catch{}}
function loadSession(){try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');return saved&&saved.url===serverURL&&/^[1-9][0-9]{3}$/.test(saved.code)&&/^[A-Za-z0-9_-]{43}$/.test(saved.token)?{code:saved.code,token:saved.token}:null;}catch{return null;}}
function resolveServer(){
  let value=String(window.VOLLEY_CONFIG?.serverUrl||'').trim();
  if(!value){if(!['http:','https:'].includes(location.protocol)||location.hostname.endsWith('github.io'))return '';value=location.origin;}
  const url=new URL(value);if(url.username||url.password||url.search||url.hash)throw Error('Некорректный адрес сервера.');
  if(url.protocol==='https:')url.protocol='wss:';else if(url.protocol==='http:')url.protocol='ws:';
  if(!['wss:','ws:'].includes(url.protocol)||(location.protocol==='https:'&&url.protocol!=='wss:'))throw Error('Для этой страницы нужен защищённый игровой сервер.');
  url.pathname=window.VOLLEY_CONFIG?.websocketPath||'/ws';return url.href;
}
function send(packet){if(socket?.readyState!==WebSocket.OPEN||!networkOK)return false;socket.send(JSON.stringify(packet));return true;}
function currentMask(){
  if(!room||room.phase!=='playing'||!$('onlineRoom').hidden||bridge.isSettingsOpen()||!networkOK)return 0;
  const input=bridge.input();
  return (input.left?1:0)|(input.right?2:0)|(input.jump?4:0)|(input.down?8:0);
}
function transmitInput(now){
  if(!session||!room||(room.phase!=='playing'&&lastMask===0))return;
  const mask=currentMask();
  if(mask===lastMask&&now-lastInputAt<1000/30)return;
  if(send({type:'input',mask,seq})){
    prediction.input(seq++,mask,now);lastInputAt=now;lastMask=mask;
  }
}
function clearInput(){pressed.clear();bridge.clear();transmitInput(performance.now());}
function showHome(text){
  $('onlineHome').hidden=false;$('onlineRoom').hidden=true;$('mainMenu').style.display='none';
  bridge.hideVictory();if(text)status(text,true);setBusy(false);
}
function showLobby(){
  if(!room)return;clearInput();$('onlineHome').hidden=true;$('onlineRoom').hidden=false;renderRoom();bridge.refreshControls();
}
function hideLobby(){if(!room||room.phase==='lobby')return;$('onlineRoom').hidden=true;clearInput();bridge.refreshControls();}
function resetRoom(text){
  room=null;role=null;session=null;snapshots=[];lastStateSeq=-1;lastEvent=0;retry=0;prediction.reset();
  clearTimeout(reconnectTimer);reconnectTimer=null;saveSession();bridge.local();showHome(text);
}
function openSocket(){
  if(socket?.readyState===WebSocket.OPEN&&networkOK)return Promise.resolve();
  if(connecting)return connecting;
  if(!serverURL)return Promise.reject(Error('Онлайн-сервер пока не подключён.'));
  manualClose=false;networkOK=false;
  connecting=new Promise((resolve,reject)=>{
    const current=new WebSocket(serverURL,'volley-v1');socket=current;
    let ready=false;
    const timer=setTimeout(()=>{reject(Error('Сервер не ответил. Попробуйте ещё раз через минуту.'));current.close();},65000);
    current.addEventListener('message',event=>{
      let packet;try{packet=JSON.parse(event.data);}catch{return;}
      if(current!==socket)return;
      if(packet.type==='hello'){
        if(packet.protocol!==1){clearTimeout(timer);reject(Error('Версии игры и сервера различаются. Обновите страницу.'));current.close();return;}
        ready=true;networkOK=true;clearTimeout(timer);resolve();ping();return;
      }
      handlePacket(packet);
    });
    current.addEventListener('error',()=>{if(!ready){clearTimeout(timer);reject(Error('Сервер недоступен. Проверьте подключение и попробуйте позже.'));}});
    current.addEventListener('close',event=>{
      clearTimeout(timer);if(!ready)reject(Error('Не удалось подключиться к игровому серверу.'));
      if(current!==socket)return;networkOK=false;connecting=null;
      clearInput();
      if(event.code===4001){manualClose=true;resetRoom('Комната открыта в другой вкладке.');return;}
      if(!manualClose&&session){showLobby();$('roomStatus').textContent='Связь потеряна. Восстанавливаем подключение…';scheduleReconnect();}
    });
  }).finally(()=>{connecting=null;});
  return connecting;
}
function scheduleReconnect(){
  if(reconnectTimer||manualClose||!session)return;
  if(retry>=6){toast('Не удалось восстановить связь. Вернитесь на главную и подключитесь заново.');$('roomStatus').textContent='Нет связи с сервером.';return;}
  const delay=[1000,2000,4000,8000,12000,15000][retry++];
  reconnectTimer=setTimeout(async()=>{
    reconnectTimer=null;
    try{await openSocket();if(session)send({type:'resume',code:session.code,token:session.token});}
    catch{scheduleReconnect();}
  },delay);
}
async function requestRoom(type){
  if(busy||room)return;
  const code=$('joinCode').value.trim();if(type==='join'&&!/^[1-9][0-9]{3}$/.test(code)){toast('Введите четырёхзначный код комнаты.');$('joinCode').focus();return;}
  setBusy(true);status('Подключаемся к серверу… Первый запуск сервера может занять около минуты.');
  try{
    await openSocket();send(type==='create'?{type:'create',color:0}:{type:'join',code,color:1});
    clearTimeout(actionTimer);actionTimer=setTimeout(()=>{setBusy(false);status('Ответ задерживается. Попробуйте снова.',true);},10000);
  }catch(error){setBusy(false);status(error.message,true);}
}
function handlePacket(packet){
  if(packet.type==='joined'){
    clearTimeout(actionTimer);retry=0;clearTimeout(reconnectTimer);reconnectTimer=null;seq=0;lastMask=-1;lastStateSeq=-1;lastEvent=0;snapshots=[];prediction.reset();
    session={code:packet.code,token:packet.token};saveSession();role=packet.role;room=packet.room;
    bridge.enter(role);bridge.room(room);pushState(packet.state,0);bridge.state(packet.state);showLobby();setBusy(false);status('Готово к игре.');return;
  }
  if(packet.type==='room'&&room){
    const previousPhase=room.phase;room=packet.room;bridge.room(room);renderRoom();
    if(room.phase==='lobby'||(room.phase==='paused'&&room.reason==='connection'))showLobby();
    else if(room.phase==='playing'&&previousPhase!=='playing')hideLobby();return;
  }
  if(packet.type==='state'&&room){if(packet.seq<=lastStateSeq)return;lastStateSeq=packet.seq;pushState(packet.state,packet.seq,packet.inputs);return;}
  if(packet.type==='pong'){
    const started=pingTimes.get(packet.id);if(started!==undefined){const sample=performance.now()-started;rtt=rtt?rtt*.8+sample*.2:sample;pingTimes.delete(packet.id);$('roomPing').textContent=Math.round(rtt)+' мс';}return;
  }
  if(packet.type==='closed'){
    manualClose=true;socket?.close();resetRoom(messages[packet.code]||'Комната закрыта.');return;
  }
  if(packet.type==='error'){
    clearTimeout(actionTimer);setBusy(false);
    if(packet.code==='SESSION_EXPIRED'){manualClose=true;socket?.close();resetRoom(messages[packet.code]);return;}
    const text=messages[packet.code]||'Действие не выполнено.';if(room)toast(text);else status(text,true);
  }
}
function pushState(state,seq,inputs){
  if(!state||!Array.isArray(state.players)||!state.ball||!Array.isArray(state.score))return;
  const now=performance.now(),index=role==='host'?0:1;
  prediction.receive(state,index,room.settings,inputs?.[index],now,rtt);
  snapshots.push({state,seq,received:now});if(snapshots.length>20)snapshots.shift();
  for(const event of state.events||[])if(event.id>lastEvent){lastEvent=event.id;bridge.effect(event);}
}
function renderRoom(){
  if(!room)return;const host=role==='host',both=room.players.every(p=>p?.connected),playing=room.phase==='playing';
  $('roomCode').textContent=room.code;$('roomRole').textContent=host?'ХОЗЯИН':'ГОСТЬ';
  $('onlineName1').textContent='Хозяин · слева'+(host?' · ты':'');$('onlineName2').textContent='Гость · справа'+(!host?' · ты':'');
  for(let i=0;i<2;i++)$('onlineStatus'+(i+1)).textContent=room.players[i]?(room.players[i].connected?'Подключён':'Восстанавливает связь · до 45 с'):'Свободное место';
  let text=!room.players[1]?'Отправь код другу и дождись подключения.':!both?'Ожидаем возвращения игрока. Матч приостановлен.':room.phase==='lobby'?(host?'Игрок подключился. Настрой параметры и начинай матч.':'Выбери цвет. Хозяин начнёт матч.'):room.phase==='paused'?'Матч на паузе. Хозяин может продолжить игру.':room.phase==='finished'?'Матч завершён. Хозяин может начать новый.':'Матч идёт.';
  if(!networkOK)text='Нет связи. Восстанавливаем подключение…';$('roomStatus').textContent=text;
  $('hostSettingsButton').hidden=!host;$('guestSettingsNote').hidden=host;
  $('hostStartButton').hidden=!host;$('hostStartButton').disabled=!both||!networkOK;
  $('hostStartButton').textContent=room.phase==='paused'?'ПРОДОЛЖИТЬ':room.phase==='playing'||room.phase==='finished'?'НОВЫЙ МАТЧ':'НАЧАТЬ МАТЧ';
  $('hostPauseButton').hidden=!host||!playing;
  $('kickGuestButton').hidden=!host||!room.players[1];$('returnToMatch').hidden=room.phase==='lobby';
  const own=room.players[host?0:1]?.color??0;
  $('ownColors').querySelectorAll('button').forEach((button,i)=>button.setAttribute('aria-pressed',String(i===own)));
  const s=room.settings;
  const values=[['Сетка',s.netHeight+' ед.'],['Скорость игроков',s.speed1+' / '+s.speed2+'%'],['Прыжки',s.jump1+' / '+s.jump2+'%'],['Мяч',s.ballSpeed+'% · размер '+s.ballSize+'%'],['Гравитация',s.gravity+'%'],['Темп',s.tempo+'%'],['Касаний',s.maxTouches||'Без лимита'],['Победа','До '+s.winScore+(s.winByTwo?' · разница 2':'')]];
  const dl=$('roomSettingsSummary');dl.replaceChildren();
  for(const [label,value]of values){const line=document.createElement('div');line.className='room-setting-line';const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;line.append(dt,dd);dl.append(line);}
}
function openRoomMenu(){if(!room)return;if(role==='host'&&room.phase==='playing')send({type:'pause',paused:true});showLobby();}
function hostSettings(){if(role!=='host'){toast(messages.NOT_HOST);return;}if(room.phase==='playing')send({type:'pause',paused:true});bridge.openSettings();}
function ownerCommand(type){if(role!=='host'){toast(messages.NOT_HOST);return;}send({type});}
function leaveRoom(){manualClose=true;send({type:'leave'});socket?.close(1000);resetRoom();status(serverURL?'Готово к игре.':'Онлайн-сервер пока не подключён.');}
function codeFromEvent(e){return e.code||({'ф':'KeyA','в':'KeyD','ц':'KeyW','ы':'KeyS'}[e.key.toLowerCase()])||e.key;}
function keyDown(e){
  if(bridge.isSettingsOpen())return;
  if(e.target&&/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName))return;
  const code=codeFromEvent(e);
  if(e.key==='Escape'){e.preventDefault();if(!$('onlineRoom').hidden&&room.phase!=='lobby')hideLobby();else openRoomMenu();return;}
  if(code==='KeyF'){e.preventDefault();bridge.fullscreen();return;}
  if(code==='KeyO'){e.preventDefault();if(!e.repeat)hostSettings();return;}
  if(code==='KeyP'){e.preventDefault();if(!e.repeat){if(role==='host')send({type:'pause',paused:room.phase!=='paused'});else toast(messages.NOT_HOST);}return;}
  if(code==='KeyR'){e.preventDefault();if(!e.repeat)ownerCommand('restart');return;}
  if(['KeyA','KeyD','KeyW','KeyS','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Space'].includes(code)){
    e.preventDefault();if(!$('onlineRoom').hidden||room.phase!=='playing')return;pressed.add(code);syncKeys();
  }
}
function syncKeys(){let mask=0;if(pressed.has('KeyA')||pressed.has('ArrowLeft'))mask|=1;if(pressed.has('KeyD')||pressed.has('ArrowRight'))mask|=2;if(pressed.has('KeyW')||pressed.has('ArrowUp')||pressed.has('Space'))mask|=4;if(pressed.has('KeyS')||pressed.has('ArrowDown'))mask|=8;bridge.setInput(mask);transmitInput(performance.now());}
function keyUp(e){pressed.delete(codeFromEvent(e));syncKeys();}
function frame(now){
  if(!room||!snapshots.length)return;
  const latest=snapshots.at(-1),renderAt=now-65;
  let a=snapshots[0],b=latest;
  for(let i=0;i<snapshots.length-1;i++)if(snapshots[i].received<=renderAt&&snapshots[i+1].received>=renderAt){a=snapshots[i];b=snapshots[i+1];break;}
  const result={...latest.state,players:latest.state.players.map(p=>({...p})),ball:{...latest.state.ball}};
  if(a.state.round===b.state.round&&a.state.round===latest.state.round&&b.received>a.received&&latest.state.phase==='playing'){
    const t=Math.max(0,Math.min(1,(renderAt-a.received)/(b.received-a.received)));
    for(let i=0;i<2;i++)for(const key of ['x','y','vx','vy','scaleX','scaleY'])result.players[i][key]=a.state.players[i][key]+(b.state.players[i][key]-a.state.players[i][key])*t;
    for(const key of ['x','y','scaleX','scaleY'])result.ball[key]=a.state.ball[key]+(b.state.ball[key]-a.state.ball[key])*t;
  }
  const mask=currentMask();transmitInput(now);
  const own=prediction.render(now,mask);
  if(own&&networkOK){
    const index=role==='host'?0:1;
    for(const key of ['x','y','vx','vy'])result.players[index][key]=own[key];
  }
  bridge.state(result);
}
Object.assign(bridge.hooks,{keyDown,keyUp,frame,inputChanged:()=>transmitInput(performance.now()),clearInput:()=>{pressed.clear();transmitInput(performance.now());},menu:openRoomMenu,settings:settings=>{if(role==='host')send({type:'settings',settings});else toast(messages.NOT_HOST);},restart:()=>ownerCommand('restart'),extend:()=>ownerCommand('extend'),canConfigure:()=>role==='host'});
$('createRoomButton').addEventListener('click',()=>requestRoom('create'));
$('joinRoomForm').addEventListener('submit',e=>{e.preventDefault();requestRoom('join');});
$('joinCode').addEventListener('input',e=>{e.target.value=e.target.value.replace(/\D/g,'').slice(0,4);});
$('localModeButton').addEventListener('click',()=>{if(session)leaveRoom();manualClose=true;socket?.close();$('onlineHome').hidden=true;$('onlineRoom').hidden=true;bridge.local();});
$('backOnlineButton').addEventListener('click',()=>{bridge.local();showHome();});
$('hostSettingsButton').addEventListener('click',hostSettings);
$('hostStartButton').addEventListener('click',()=>{if(room.phase==='paused')send({type:'pause',paused:false});else ownerCommand(room.phase==='lobby'?'start':'restart');});
$('hostPauseButton').addEventListener('click',()=>send({type:'pause',paused:true}));
$('returnToMatch').addEventListener('click',hideLobby);$('leaveRoomButton').addEventListener('click',leaveRoom);
$('kickGuestButton').addEventListener('click',()=>ownerCommand('kick'));
$('copyRoomCode').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(room.code);toast('Код комнаты скопирован.');}catch{toast('Код комнаты: '+room.code);}});
COLORS.forEach((color,index)=>{
  const button=document.createElement('button');button.type='button';button.className='color-swatch';button.setAttribute('aria-label',names[index]);button.setAttribute('aria-pressed','false');button.style.setProperty('--swatch',color);
  button.append(document.createElement('span'));button.addEventListener('click',()=>send({type:'color',color:index}));$('ownColors').append(button);
});
let pingId=0;
function ping(){if(!networkOK)return;const id=pingId++%2147483647;pingTimes.clear();pingTimes.set(id,performance.now());send({type:'ping',id});}
setInterval(()=>{if(room)ping();},3000);
window.addEventListener('pagehide',()=>{clearInput();});
try{serverURL=resolveServer();status(serverURL?'Создай комнату или введи код друга.':'Онлайн-сервер пока не подключён. Доступен локальный режим.',!serverURL);}catch(error){status(error.message,true);}
const codeMatch=location.hash.match(/^#room=([1-9][0-9]{3})$/);if(codeMatch)$('joinCode').value=codeMatch[1];
showHome();session=loadSession();
if(session){setBusy(true);status('Восстанавливаем комнату…');openSocket().then(()=>send({type:'resume',code:session.code,token:session.token})).catch(()=>{setBusy(false);status('Не удалось восстановить связь с сервером.',true);scheduleReconnect();});}
