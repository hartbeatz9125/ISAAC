(() => {
  const canvas = document.querySelector('.fx');
  const ctx = canvas.getContext('2d', { alpha: true });
  const W = 1920, H = 1080;
  let mx = 0, my = 0, tx = 0, ty = 0;
  let energy = 0, targetEnergy = 0, isaacState = 'idle';
  const rings = [
    {x:938,y:192,rx:350,ry:73,a:-.05,s:.032,p:0,o:.23},
    {x:942,y:302,rx:480,ry:122,a:-.03,s:-.019,p:1.4,o:.18},
    {x:944,y:395,rx:370,ry:96,a:.04,s:.024,p:2.5,o:.28},
    {x:944,y:521,rx:330,ry:86,a:-.02,s:-.028,p:.7,o:.22},
    {x:944,y:641,rx:310,ry:73,a:.02,s:.021,p:3.1,o:.22},
    {x:940,y:820,rx:350,ry:75,a:0,s:-.016,p:4,o:.18}
  ];
  const particles = Array.from({length:95},(_,i)=>({x:Math.random()*W,y:Math.random()*H,z:Math.random(),r:Math.random()*1.4+.3,v:Math.random()*4+1,p:i*.71}));
  const nodes = Array.from({length:18},(_,i)=>({ring:i%rings.length,phase:i*2.399,size:i%4===0?3.2:1.8}));
  function resize(){canvas.width=W*devicePixelRatio;canvas.height=H*devicePixelRatio;canvas.style.width='100%';canvas.style.height='100%';ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0)}
  addEventListener('resize',resize); resize();
  addEventListener('mousemove',e=>{tx=(e.clientX/innerWidth-.5)*2;ty=(e.clientY/innerHeight-.5)*2});
  addEventListener('isaac-state',e=>{isaacState=e.detail;targetEnergy=e.detail==='speaking'?1:e.detail==='thinking'?.48:0});
  function ellipsePoint(r,phase){const c=Math.cos(phase),s=Math.sin(phase),ca=Math.cos(r.a),sa=Math.sin(r.a);return{x:r.x+r.rx*c*ca-r.ry*s*sa,y:r.y+r.rx*c*sa+r.ry*s*ca}}
  function draw(time){
    const t=time/1000; mx+=(tx-mx)*.035; my+=(ty-my)*.035;const attack=isaacState==='speaking'?.34:.10;energy+=(targetEnergy-energy)*(targetEnergy>energy?attack:.035);const speech=isaacState==='speaking'?(.82+Math.sin(t*5.7)*.14+Math.sin(t*9.3+1.2)*.08):0;const activeEnergy=energy*(isaacState==='speaking'?speech:1); ctx.clearRect(0,0,W,H);
    // background dust: barely responsive, slow drift
    for(const p of particles){const depth=.25+p.z*.75;const x=(p.x+mx*5*depth+Math.sin(t*.11+p.p)*4*depth+W)%W;const y=(p.y+my*3*depth-t*p.v+H)%H;ctx.globalAlpha=.06+p.z*.22;ctx.fillStyle=p.p%5<1?'#ff5b38':'#67d9ff';ctx.fillRect(x,y,p.r*depth,p.r*depth)}
    ctx.save();ctx.translate(mx*7,my*5);
    // independent orbital assemblies aligned to rendered machine
    for(const r of rings){
      const phase=t*r.s*10+r.p;ctx.save();ctx.translate(r.x,r.y);ctx.rotate(r.a);ctx.beginPath();ctx.ellipse(0,0,r.rx,r.ry,0,phase+.25,phase+1.75);ctx.strokeStyle=`rgba(245,253,255,${r.o*1.22})`;ctx.lineWidth=1.35;ctx.shadowColor='#ffffff';ctx.shadowBlur=9;ctx.stroke();ctx.beginPath();ctx.ellipse(0,0,r.rx,r.ry,0,phase+3.25,phase+4.2);ctx.strokeStyle=`rgba(255,255,255,${r.o})`;ctx.stroke();ctx.restore();
    }
    for(const n of nodes){const r=rings[n.ring],p=ellipsePoint(r,t*r.s*10+n.phase);const blink=.45+.55*Math.sin(t*1.7+n.phase)**2;ctx.globalAlpha=.25+.65*blink;ctx.fillStyle='#ffffff';ctx.shadowColor='#ffffff';ctx.shadowBlur=13;ctx.beginPath();ctx.arc(p.x,p.y,n.size,0,Math.PI*2);ctx.fill()}
    // energy conduit and chamber pulses
    const pulse=.5+.5*Math.sin(t*(1.3+energy*.55));let grad=ctx.createLinearGradient(0,85,0,825);grad.addColorStop(0,'rgba(238,250,255,0)');grad.addColorStop(.42,`rgba(242,252,255,${.13+pulse*.12+activeEnergy*.24})`);grad.addColorStop(.6,`rgba(255,255,255,${.32+pulse*.24+activeEnergy*.48})`);grad.addColorStop(1,'rgba(205,241,255,0)');ctx.globalAlpha=1;ctx.fillStyle=grad;ctx.shadowColor='#ffffff';ctx.shadowBlur=24+pulse*22+activeEnergy*58;ctx.fillRect(932-activeEnergy*7,70,18+activeEnergy*14,780);
    const chamber=ctx.createRadialGradient(942,491,0,942,491,116+activeEnergy*42);chamber.addColorStop(0,`rgba(255,255,255,${.38+pulse*.22+activeEnergy*.54})`);chamber.addColorStop(.18,`rgba(255,255,255,${.27+pulse*.15+activeEnergy*.38})`);chamber.addColorStop(.46,`rgba(226,246,252,${.09+pulse*.05+activeEnergy*.18})`);chamber.addColorStop(1,'rgba(40,133,173,0)');ctx.fillStyle=chamber;ctx.shadowColor='#ffffff';ctx.shadowBlur=18+activeEnergy*55;ctx.fillRect(760,309,364,364);
    if(activeEnergy>.04){const spill=ctx.createRadialGradient(942,720,5,942,720,235+activeEnergy*55);spill.addColorStop(0,`rgba(255,255,255,${activeEnergy*.32})`);spill.addColorStop(.36,`rgba(238,250,255,${activeEnergy*.17})`);spill.addColorStop(1,'rgba(210,243,255,0)');ctx.fillStyle=spill;ctx.shadowBlur=0;ctx.fillRect(640,560,604,360)}
    // holographic base breath
    ctx.globalAlpha=.16+pulse*.12+activeEnergy*.24;ctx.strokeStyle='#ffffff';ctx.shadowColor='#ffffff';ctx.lineWidth=2.2+activeEnergy*1.8;ctx.shadowBlur=16+activeEnergy*32;ctx.beginPath();ctx.ellipse(941,831,281+pulse*5,51+pulse*2,0,0,Math.PI*2);ctx.stroke();
    // restrained diagnostic sweep crossing only the machine zone
    const sy=120+(t*34)%690;const sweep=ctx.createLinearGradient(0,sy-28,0,sy+28);sweep.addColorStop(0,'rgba(65,214,255,0)');sweep.addColorStop(.5,'rgba(111,232,255,.09)');sweep.addColorStop(1,'rgba(65,214,255,0)');ctx.fillStyle=sweep;ctx.shadowBlur=0;ctx.fillRect(520,sy-28,850,56);
    ctx.restore();ctx.globalAlpha=1;requestAnimationFrame(draw)
  }
  requestAnimationFrame(draw);
})();
