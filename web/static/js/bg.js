// Animated particle mesh + scroll reveal. Static single frame under reduced motion.
(()=>{
const rm=matchMedia('(prefers-reduced-motion: reduce)').matches;
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.12});
document.querySelectorAll('.rv').forEach(el=>rm?el.classList.add('in'):io.observe(el));
const c=document.getElementById('bg');if(!c)return;
const x=c.getContext('2d');let w,h,P=[];const cols=['66,133,244','234,67,53','251,188,4','52,168,83'];
function size(){w=c.width=innerWidth;h=c.height=innerHeight;const n=Math.min(70,Math.floor(w*h/22000));
P=Array.from({length:n},(_,i)=>({x:Math.random()*w,y:Math.random()*h,vx:(Math.random()-.5)*.35,vy:(Math.random()-.5)*.35,c:cols[i%4]}))}
function draw(){x.clearRect(0,0,w,h);
const g=x.createRadialGradient(w*.5,h*.1,0,w*.5,h*.1,w*.7);g.addColorStop(0,'rgba(66,133,244,.16)');g.addColorStop(1,'rgba(5,6,10,0)');x.fillStyle=g;x.fillRect(0,0,w,h);
for(const p of P){if(!rm){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>w)p.vx*=-1;if(p.y<0||p.y>h)p.vy*=-1}
x.fillStyle=`rgba(${p.c},.9)`;x.beginPath();x.arc(p.x,p.y,1.8,0,7);x.fill()}
for(let i=0;i<P.length;i++)for(let j=i+1;j<P.length;j++){const d=Math.hypot(P[i].x-P[j].x,P[i].y-P[j].y);
if(d<140){x.strokeStyle=`rgba(${P[i].c},${.22*(1-d/140)})`;x.beginPath();x.moveTo(P[i].x,P[i].y);x.lineTo(P[j].x,P[j].y);x.stroke()}}
if(!rm)requestAnimationFrame(draw)}
size();draw();addEventListener('resize',()=>{size();if(rm)draw()});
})();
