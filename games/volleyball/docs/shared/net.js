// Shared by local play, the reference match and snapshot rendering. The net is
// a vertical capsule, including its round cap. Sweep the entire displacement,
// including a player's push, so a fast ball cannot skip to the other side.
(() => {
  const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
  function resolve(ball,net,oldX,oldY,ground=585) {
    const radius=ball.radius+net.width, dx=ball.x-oldX, dy=ball.y-oldY;
    if(Math.max(oldX,ball.x)<net.x-radius||Math.min(oldX,ball.x)>net.x+radius
      ||Math.max(oldY,ball.y)<net.y-radius||Math.min(oldY,ball.y)>ground+radius)return false;
    let hit=null;
    const take=(t,nx,ny)=>{
      if(t>=-1e-9&&t<=1&&(!hit||t<hit.t))hit={t:Math.max(0,t),nx,ny};
    };
    const startY=clamp(oldY,net.y,ground);
    if(Math.hypot(oldX-net.x,oldY-startY)>=radius-1e-9){
      if(dx!==0)for(const nx of [-1,1]){
        if(dx*nx>=0)continue;
        const t=(net.x+nx*radius-oldX)/dx, y=oldY+dy*t;
        if(y>=net.y&&y<=ground)take(t,nx,0);
      }
      const a=dx*dx+dy*dy;
      if(a>0)for(const y of [net.y,ground]){
        const ox=oldX-net.x,oy=oldY-y,b=2*(ox*dx+oy*dy);
        const disc=b*b-4*a*(ox*ox+oy*oy-radius*radius);
        if(disc<0)continue;
        const t=(-b-Math.sqrt(disc))/(2*a);
        const nx=(ox+dx*t)/radius,ny=(oy+dy*t)/radius;
        if(dx*nx+dy*ny<0)take(t,nx,ny);
      }
    }
    if(hit){
      ball.x=oldX+dx*hit.t+hit.nx*.1;ball.y=oldY+dy*hit.t+hit.ny*.1;
    }else{
      const y=clamp(ball.y,net.y,ground),xDelta=ball.x-net.x,yDelta=ball.y-y;
      const distance=Math.hypot(xDelta,yDelta);
      if(distance>=radius)return false;
      const nx=distance>1e-9?xDelta/distance:(oldX<=net.x?-1:1);
      const ny=distance>1e-9?yDelta/distance:0;
      hit={nx,ny};ball.x=net.x+nx*(radius+.1);ball.y=y+ny*(radius+.1);
    }
    const {nx,ny}=hit,approach=ball.vx*nx+ball.vy*ny;
    if(approach<0){
      const restitution=ny<-.5?.8:.7;
      ball.vx-=(1+restitution)*approach*nx;ball.vy-=(1+restitution)*approach*ny;
      net.bendVel=clamp(-approach*nx,-25,25);
      ball.scaleX=ny<-.5?1.37:.72;ball.scaleY=ny<-.5?.63:1.28;
      ball.sVelX=(1-ball.scaleX)*.2;ball.sVelY=(1-ball.scaleY)*.2;
    }
    if(ny<-.5&&Math.abs(ball.vx)<1.5)ball.vx+=ball.x>=net.x?3:-3;
    return {nx,ny,approach};
  }
  globalThis.VolleyNet=Object.freeze({resolve});
})();
