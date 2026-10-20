import { DEFAULT_SETTINGS, normalizeSettings } from './settings.js';
import './net.js';
export const GROUND = 585;
export const DT = 1000 / 60;
const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
const jelly=(p,x,y)=>{p.scaleX=x;p.scaleY=y;p.sVelX=(1-x)*.2;p.sVelY=(1-y)*.2;};
function animate(p){p.sVelX+=(1-p.scaleX)*.15;p.sVelY+=(1-p.scaleY)*.15;p.sVelX*=.8;p.sVelY*=.8;p.scaleX+=p.sVelX;p.scaleY+=p.sVelY;}
const shape=()=>({scaleX:1,scaleY:1,sVelX:0,sVelY:0});

// Используется сервером. Клиенту разрешено предсказывать только видимое движение своего персонажа.
export function movePlayer(p,mask,index,settings) {
  const left=Boolean(mask&1),right=Boolean(mask&2),jump=Boolean(mask&4),down=Boolean(mask&8);
  const speed=settings[index===0?'speed1':'speed2']/100;
  const accel=1.8*settings.acceleration/100*speed;
  if(left)p.vx-=accel;if(right)p.vx+=accel;if(!left&&!right)p.vx*=.85;
  if(jump&&p.y===GROUND){p.vy=p.baseJump;jelly(p,.6,1.4);}
  if(!jump&&p.vy< -5)p.vy*=.8;
  if(down&&!jump&&p.y<GROUND)p.vy+=2.5;
  p.vx=clamp(p.vx,-12*speed,12*speed);
  const airborne=p.y<GROUND;
  p.vy+=.45*settings.gravity/100;p.x+=p.vx;p.y+=p.vy;
  if(p.y>=GROUND){if(airborne)jelly(p,1.4,.6);p.y=GROUND;p.vy=0;}
  const middle=settings.courtWidth/2;
  if(index===0){if(p.x<p.radius){p.x=p.radius;p.vx*=-.5;}if(p.x>middle-p.radius){p.x=middle-p.radius;p.vx=0;}}
  else{if(p.x<middle+p.radius){p.x=middle+p.radius;p.vx=0;}if(p.x>settings.courtWidth-p.radius){p.x=settings.courtWidth-p.radius;p.vx*=-.5;}}
}
export class Match {
  constructor(settings=DEFAULT_SETTINGS){
    this.settings=normalizeSettings(settings);this.phase='lobby';this.score=[0,0];this.tick=0;this.time=0;this.round=0;
    this.events=[];this.eventId=0;this.accumulator=0;this.target=this.settings.winScore;
    this.players=[0,1].map(()=>({x:0,y:GROUND,vx:0,vy:0,radius:45,baseJump:-16,hitTimer:0,...shape()}));
    this.ball={x:0,y:0,vx:0,vy:0,radius:40,...shape()};this.net={x:0,y:0,height:0,width:8,bend:0,bendVel:0};
    this.configure();this.resetPositions();this.resetRound(1);
  }
  configure(){
    const s=this.settings;this.gravity=.45*s.gravity/100;this.drag=1-s.airDrag/100;
    this.players[0].baseJump=-16*s.jump1/100;this.players[1].baseJump=-16*s.jump2/100;
    this.ball.radius=40*s.ballSize/100;Object.assign(this.net,{x:s.courtWidth/2,y:GROUND-s.netHeight,height:s.netHeight,width:s.netWidth});
  }
  resetPositions(){
    this.players.forEach((p,i)=>Object.assign(p,{x:this.settings.courtWidth*(i===0?.25:.75),y:GROUND,vx:0,vy:0,hitTimer:0,...shape()}));
    Object.assign(this.ball,shape());
  }
  resetRound(server){
    this.server=server;this.serving=true;this.possession=0;this.touches=0;this.cooldown=0;this.combo=0;this.round++;
    this.net.bend=this.net.bendVel=0;this.ball.vx=this.ball.vy=0;
    const p=this.players[server-1];const reach=p.radius*2.8+this.ball.radius+p.baseJump**2/(2*this.gravity)*.65;
    this.ball.x=this.settings.courtWidth*(server===1?.25:.75);this.ball.y=GROUND-Math.min(this.settings.serveHeight,reach,GROUND-this.ball.radius-35);
  }
  start(){this.score=[0,0];this.target=this.settings.winScore;this.phase='playing';this.accumulator=0;this.configure();this.resetPositions();this.resetRound(1);this.emit('start');}
  applySettings(settings){
    this.settings=normalizeSettings(settings);this.target=this.settings.winScore;this.configure();this.resetPositions();this.resetRound(this.server);
    if(this.phase!=='lobby'&&this.hasWinner()){this.phase='finished';this.emit('win',{winner:this.score[0]>this.score[1]?1:2});}
  }
  extend(){this.target=Math.max(this.target,...this.score)+5;this.phase='playing';this.resetPositions();this.resetRound(1);this.emit('start');}
  hasWinner(){return Math.max(...this.score)>=this.target&&(!this.settings.winByTwo||Math.abs(this.score[0]-this.score[1])>=2);}
  emit(kind,data={}){this.events.push({id:++this.eventId,kind,...data});if(this.events.length>12)this.events.shift();}
  point(winner,reason='floor'){
    const at={x:this.ball.x,y:GROUND};this.score[winner-1]++;this.emit('point',{winner,reason,...at});
    if(this.hasWinner()){this.phase='finished';this.emit('win',{winner});}
    else this.resetRound(winner);
  }
  advance(elapsed,inputs){
    if(this.phase!=='playing'){this.accumulator=0;return;}
    this.accumulator+=Math.min(100,Math.max(0,elapsed))*this.settings.tempo/100;
    let steps=0;
    while(this.accumulator>=DT&&steps++<12&&this.phase==='playing'){this.step(inputs);this.accumulator-=DT;}
  }
  moveBall(){
    if(this.serving)return;
    const b=this.ball,n=this.net,s=this.settings;const ballDt=s.ballSpeed/100;
    const steps=Math.min(40,Math.max(1,Math.ceil(Math.hypot(b.vx,b.vy)*ballDt/Math.max(4,Math.min(b.radius/2,n.width)))));
    const dt=ballDt/steps;
    for(let step=0;step<steps;step++){
      b.vx*=this.drag**dt;b.vy*=this.drag**dt;b.vy+=this.gravity*dt;
      const limit=20*Math.max(1,s.hitPower/100);b.vy=clamp(b.vy,-limit,limit);
      const oldX=b.x,oldY=b.y;b.x+=b.vx*dt;b.y+=b.vy*dt;
      if(b.x<b.radius){b.x=b.radius;b.vx=Math.abs(b.vx);jelly(b,.63,1.37);}
      if(b.x>s.courtWidth-b.radius){b.x=s.courtWidth-b.radius;b.vx=-Math.abs(b.vx);jelly(b,.63,1.37);}
      globalThis.VolleyNet.resolve(b,n,oldX,oldY);
    }
  }
  step(inputs=[0,0]){
    if(this.phase!=='playing')return;
    this.tick++;this.time+=.05;if(this.cooldown>0)this.cooldown--;
    animate(this.ball);for(const p of this.players){animate(p);if(p.hitTimer>0)p.hitTimer--;}
    this.net.bendVel+=(0-this.net.bend)*.1;this.net.bendVel*=.85;this.net.bend+=this.net.bendVel;
    this.players.forEach((p,i)=>movePlayer(p,inputs[i]||0,i,this.settings));this.moveBall();
    const b=this.ball;
    for(let i=0;i<2;i++){
      const p=this.players[i],top=p.y-p.radius*2.8*p.scaleY,bottom=p.y-p.radius*.5;
      const closestY=clamp(b.y,top,bottom);let dx=b.x-p.x,dy=b.y-closestY;
      const combined=b.radius+p.radius*p.scaleX*1.1;if(dx*dx+dy*dy>=combined*combined)continue;
      this.serving=false;let distance=Math.hypot(dx,dy);if(distance===0){dx=0;dy=-1;distance=1;}
      const nx=dx/distance,ny=dy/distance,power=this.settings.hitPower/100;
      const bounce=Math.min(Math.hypot(b.vx,b.vy)*.8+10,18)*power;
      const oldX=b.x,oldY=b.y;
      b.vx=nx*bounce+p.vx*.5;b.vy=ny*bounce-(closestY<p.y-p.radius?9:4)*power;
      b.x=p.x+nx*(combined+.1);b.y=closestY+ny*(combined+.1);
      p.hitTimer=15;jelly(b,1.37,.63);jelly(p,1.2,.8);
      globalThis.VolleyNet.resolve(b,this.net,oldX,oldY);
      if(this.cooldown===0){
        this.combo++;if(this.possession!==i+1){this.possession=i+1;this.touches=1;}else this.touches++;
        this.emit('hit',{player:i+1,x:b.x,y:b.y,touches:this.touches,combo:this.combo});
        if(this.settings.maxTouches!==0&&this.touches>this.settings.maxTouches){this.point(i===0?2:1,'touches');return;}
        this.cooldown=15;
      }
    }
    if(b.y+b.radius>GROUND)this.point(b.x<this.settings.courtWidth/2?2:1);
  }
  snapshot(){
    const pick=p=>Object.fromEntries(['x','y','vx','vy','radius','baseJump','scaleX','scaleY','hitTimer'].filter(k=>k in p).map(k=>[k,Number(p[k].toFixed(3))]));
    return {tick:this.tick,time:Number(this.time.toFixed(3)),round:this.round,phase:this.phase,score:[...this.score],target:this.target,serving:this.serving,server:this.server,touches:this.touches,possession:this.possession,players:this.players.map(pick),ball:pick(this.ball),bend:Number(this.net.bend.toFixed(3)),events:[...this.events]};
  }
}
