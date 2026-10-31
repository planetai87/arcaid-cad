const rad = degrees => degrees*Math.PI/180;
export function arcPoints(center,radius,start,end) {
  const sweep = ((end-start)%360+360)%360 || 360;
  const count = Math.max(4,Math.ceil(sweep/4));
  return Array.from({length:count+1},(_,i)=>{
    const angle=rad(start+sweep*i/count);
    return [center[0]+radius*Math.cos(angle),center[1]+radius*Math.sin(angle)];
  });
}
export function polylinePoints(points=[],bulges=[],closed=false) {
  if (!points.length) return [];
  const out=[points[0]];
  const count=closed?points.length:points.length-1;
  for(let i=0;i<count;i++) {
    const a=points[i],b=points[(i+1)%points.length],bulge=bulges[i]||0;
    const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
    if(Math.abs(bulge)<1e-10 || length<1e-10) {out.push(b);continue;}
    const offset=length*(1-bulge*bulge)/(4*bulge);
    const center=[(a[0]+b[0])/2-dy/length*offset,(a[1]+b[1])/2+dx/length*offset];
    const radius=length*(1+bulge*bulge)/(4*Math.abs(bulge));
    const angle=Math.atan2(a[1]-center[1],a[0]-center[0]);
    const sweep=4*Math.atan(bulge),n=Math.max(4,Math.ceil(Math.abs(sweep)/(Math.PI/45)));
    for(let j=1;j<=n;j++) out.push([center[0]+radius*Math.cos(angle+sweep*j/n),center[1]+radius*Math.sin(angle+sweep*j/n)]);
  }
  return out;
}
export function dimensionGeometry(p) {
  const [p1,p2,origin]=p.points,angle=rad(p.angle||0),u=[Math.cos(angle),Math.sin(angle)];
  const project=point=>{const t=(point[0]-origin[0])*u[0]+(point[1]-origin[1])*u[1];return [origin[0]+u[0]*t,origin[1]+u[1]*t];};
  return {a:project(p1),b:project(p2),value:Math.abs((p2[0]-p1[0])*u[0]+(p2[1]-p1[1])*u[1])};
}
export function pointsOf(p) {
  if(p.kind==='circle') return [[p.center[0]-p.radius,p.center[1]-p.radius],[p.center[0]+p.radius,p.center[1]+p.radius]];
  if(p.kind==='arc') return arcPoints(p.center,p.radius,p.start_angle,p.end_angle);
  if(p.kind==='polyline') return polylinePoints(p.points,p.bulges,p.closed);
  if(p.kind==='hatch') return (p.loops||[]).flat();
  if(p.kind==='text') {
    const h=p.height||120,w=h*(p.text||'').length,align=p.align||'L',horizontal=align.at(-1);
    const left=horizontal==='C'?-w/2:horizontal==='R'?-w:0;
    const bottom=align[0]==='T'?-h:align[0]==='M'?-h/2:0,angle=rad(p.rotation||0);
    return [[left,bottom],[left+w,bottom],[left+w,bottom+h],[left,bottom+h]].map(([x,y])=>[
      p.at[0]+x*Math.cos(angle)-y*Math.sin(angle),p.at[1]+x*Math.sin(angle)+y*Math.cos(angle)]);
  }
  if(p.at) {
    const h=p.height||100,w=h*(p.text||p.name||'').length;
    return [[p.at[0]-w,p.at[1]-h],[p.at[0]+w,p.at[1]+h]];
  }
  return p.points||[];
}
export function boundsOf(prims) {
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const p of prims) for(const [x,y] of pointsOf(p)) {
    if(!Number.isFinite(x)||!Number.isFinite(y)) continue;
    minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);
  }
  return minX===Infinity?{minX:0,minY:0,maxX:12000,maxY:10000}:{minX,minY,maxX,maxY};
}
const shapeKeys=['kind','points','bulges','closed','center','radius','start_angle','end_angle','at','text','height','align','rotation','name','xscale','yscale','attribs','pattern','pattern_scale','pattern_angle','loops','angle'];
export const geometrySignature = p => JSON.stringify(shapeKeys.map(key=>p[key]??null));
function segmentDistance(p,a,b) {
  const dx=b[0]-a[0],dy=b[1]-a[1],length=dx*dx+dy*dy;
  const t=length?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)):0;
  return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
}
export function hitDistance(p,point) {
  if(p.kind==='circle') return Math.abs(Math.hypot(point[0]-p.center[0],point[1]-p.center[1])-p.radius);
  if(p.kind==='text') {
    const h=p.height||120,w=(p.text||'').length*h,angle=rad(p.rotation||0);
    const dx=point[0]-p.at[0],dy=point[1]-p.at[1];
    const x=dx*Math.cos(angle)+dy*Math.sin(angle),y=-dx*Math.sin(angle)+dy*Math.cos(angle);
    const align=p.align||'L',horizontal=align.at(-1);
    const left=horizontal==='C'?-w/2:horizontal==='R'?-w:0;
    const bottom=align[0]==='T'?-h:align[0]==='M'?-h/2:0;
    return Math.hypot(Math.max(left-x,0,x-left-w),Math.max(bottom-y,0,y-bottom-h));
  }
  if(p.at) return Math.hypot(point[0]-p.at[0],point[1]-p.at[1]);
  const loops=p.kind==='hatch'?p.loops:[pointsOf(p)];
  let distance=Infinity;
  for(const points of loops||[]) for(let i=1;i<points.length;i++) distance=Math.min(distance,segmentDistance(point,points[i-1],points[i]));
  return distance;
}
export function visibleIn(p,view,size) {
  const b=p._bounds||boundsOf([p]),pad=25;
  return b.maxX*view.scale+view.x>=-pad && b.minX*view.scale+view.x<=size.width+pad &&
    -b.minY*view.scale+view.y>=-pad && -b.maxY*view.scale+view.y<=size.height+pad;
}
