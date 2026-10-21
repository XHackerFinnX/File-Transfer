// One cached renderer for both menu previews and the ball on the court.
// Designs never read or change match physics.
(() => {
  const designs=Object.freeze([
    {id:'classic',label:'Классический'},
    {id:'match',label:'Сине-жёлтый'},
    {id:'beach',label:'Пляжный'},
    {id:'basketball',label:'Баскетбольный'},
    {id:'football',label:'Футбольный'},
    {id:'neon',label:'Неон'}
  ].map(Object.freeze));
  function render(canvas,id='classic'){
    const c=canvas.getContext('2d'),r=42,tau=Math.PI*2;
    c.clearRect(0,0,canvas.width,canvas.height);c.save();
    c.scale(canvas.width/100,canvas.height/100);c.translate(50,50);
    c.beginPath();c.arc(0,0,r,0,tau);c.save();c.clip();
    c.fillStyle=id==='basketball'?'#f39132':id==='neon'?'#101a33':id==='match'?'#ffda3d':'#fff';
    c.fillRect(-r,-r,r*2,r*2);
    c.strokeStyle='#111';c.lineWidth=3;c.lineCap='round';c.lineJoin='round';
    if(id==='match'){
      for(let i=0;i<3;i++){
        c.save();c.rotate(i*tau/3);c.fillStyle='#2166d1';
        c.beginPath();c.moveTo(0,-r);c.quadraticCurveTo(r,-r,r,0);
        c.quadraticCurveTo(0,-r*.2,0,r);c.quadraticCurveTo(-r*.65,0,0,-r);c.fill();c.stroke();c.restore();
      }
    }else if(id==='beach'){
      for(let i=0;i<6;i++){
        c.fillStyle=['#f96868','#fff0c4','#4c9cde'][i%3];
        c.beginPath();c.moveTo(0,0);c.arc(0,0,r,i*tau/6,(i+1)*tau/6);c.closePath();c.fill();c.stroke();
      }
      c.beginPath();c.arc(0,0,7,0,tau);c.fillStyle='#fff0c4';c.fill();c.stroke();
    }else if(id==='basketball'){
      c.beginPath();c.moveTo(-r,0);c.lineTo(r,0);c.moveTo(0,-r);c.lineTo(0,r);c.stroke();
      for(const side of [-1,1]){
        c.beginPath();c.moveTo(side*r*.65,-r);c.bezierCurveTo(-side*r*.4,-r*.4,-side*r*.4,r*.4,side*r*.65,r);c.stroke();
      }
    }else if(id==='football'){
      const pentagon=(x,y,size,angle)=>{
        c.beginPath();for(let i=0;i<5;i++){
          const a=angle+i*tau/5,px=x+Math.cos(a)*size,py=y+Math.sin(a)*size;
          if(i)c.lineTo(px,py);else c.moveTo(px,py);
        }
        c.closePath();c.fillStyle='#17202b';c.fill();c.stroke();
      };
      for(let i=0;i<5;i++){
        const a=-Math.PI/2+i*tau/5,x=Math.cos(a),y=Math.sin(a);
        c.beginPath();c.moveTo(x*14,y*14);c.lineTo(x*r,y*r);c.stroke();
        pentagon(x*r,y*r,13,a+Math.PI);
      }
      pentagon(0,0,15,-Math.PI/2);
    }else if(id==='neon'){
      c.lineWidth=6;c.strokeStyle='#30e0ec';
      c.beginPath();c.ellipse(0,0,r*.45,r*.95,-Math.PI/4,0,tau);c.stroke();
      c.strokeStyle='#b37dff';c.beginPath();c.ellipse(0,0,r*.45,r*.95,Math.PI/4,0,tau);c.stroke();
      c.fillStyle='#e1fcff';c.beginPath();c.arc(0,0,5,0,tau);c.fill();
    }else{
      c.lineWidth=6;
      c.beginPath();c.moveTo(-r,0);c.quadraticCurveTo(0,-r/2,r,0);c.stroke();
      for(const side of [-1,1]){
        c.beginPath();c.moveTo(-r*.7,side*r*.7);c.quadraticCurveTo(0,r/2,r*.7,side*r*.7);c.stroke();
      }
    }
    c.restore();c.beginPath();c.arc(0,0,r,0,tau);
    c.lineWidth=6;c.strokeStyle=id==='neon'?'#305d86':'#000';c.stroke();c.restore();
  }
  globalThis.VolleyBalls=Object.freeze({designs,render});
})();
