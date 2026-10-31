import React, { memo } from 'react';
import { Shape } from 'react-konva';
import { polylinePoints, arcPoints, dimensionGeometry } from '../utils/geometry.js';

const geometry = new WeakMap();
function prepared(p) {
  if(geometry.has(p)) return geometry.get(p);
  const value={...p};
  if(p.kind==='polyline') value.vertices=polylinePoints(p.points,p.bulges,p.closed);
  if(p.kind==='arc') value.vertices=arcPoints(p.center,p.radius,p.start_angle,p.end_angle);
  if(p.kind==='dim' && p.points?.length===3) value.dimension=dimensionGeometry(p);
  geometry.set(p,value);return value;
}
function path(ctx,points,closed=false) {
  if(!points?.length) return;
  ctx.moveTo(points[0][0],-points[0][1]);
  for(let i=1;i<points.length;i++) ctx.lineTo(points[i][0],-points[i][1]);
  if(closed) ctx.closePath();
}
function label(ctx,p,text,height=p.height||120,at=p.at,rotation=p.rotation||0,align=p.align||'L') {
  ctx.save();ctx.translate(at[0],-at[1]);ctx.rotate(-rotation*Math.PI/180);
  ctx.font=height+'px "Malgun Gothic", sans-serif';
  const horizontal=align.at(-1);
  ctx.textAlign=horizontal==='C'?'center':horizontal==='R'?'right':'left';
  ctx.textBaseline=align[0]==='T'?'top':align[0]==='M'?'middle':align[0]==='B'?'bottom':'alphabetic';
  ctx.fillText(text,0,0);ctx.restore();
}
export function paint(ctx,original,color,width,hit=false) {
  const p=prepared(original);
  ctx.save();ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=width;ctx.lineJoin='round';
  ctx.beginPath();
  if(p.kind==='line') path(ctx,p.points);
  if(p.kind==='polyline'||p.kind==='arc') path(ctx,p.vertices,p.closed);
  if(p.kind==='circle') ctx.arc(p.center[0],-p.center[1],p.radius,0,Math.PI*2);
  if(p.kind==='hatch') {
    for(const loop of p.loops||[]) path(ctx,loop,true);
    if(!hit) {ctx.globalAlpha=0.13;ctx.fill('evenodd');ctx.globalAlpha=1;}
  }
  if(p.kind==='text') {
    if(hit) {
      const h=p.height||120;ctx.font=h+'px "Malgun Gothic", sans-serif';
      const w=ctx.measureText(p.text||'').width,align=p.align||'L',horizontal=align.at(-1);
      const left=horizontal==='C'?-w/2:horizontal==='R'?-w:0;
      const top=align[0]==='T'?0:align[0]==='M'?-h/2:-h;
      ctx.save();ctx.translate(p.at[0],-p.at[1]);ctx.rotate(-(p.rotation||0)*Math.PI/180);
      ctx.rect(left,top,w,h);ctx.restore();
    }
    else label(ctx,p,p.text||'');
  }
  if(p.kind==='block') {
    const [x,y]=p.at,r=width*5;
    ctx.moveTo(x-r,-y);ctx.lineTo(x+r,-y);ctx.moveTo(x,-y-r);ctx.lineTo(x,-y+r);
    ctx.arc(x,-y,r,0,Math.PI*2);
    if(!hit) label(ctx,p,p.name||'블록',Math.max(90,width*9),[x+r,y+r]);
  }
  if(p.dimension) {
    const {a,b,value}=p.dimension;
    path(ctx,[p.points[0],a]);path(ctx,[p.points[1],b]);path(ctx,[a,b]);
    const r=width*4;
    for(const end of [a,b]) path(ctx,[[end[0]-r,end[1]-r],[end[0]+r,end[1]+r]]);
    if(!hit) label(ctx,p,value.toLocaleString('ko-KR',{maximumFractionDigits:2}),p.height||120,
      [(a[0]+b[0])/2,(a[1]+b[1])/2+50],p.angle||0,'BC');
  }
  if(!hit) ctx.stroke();
  ctx.restore();
}
export default memo(function Primitive({p,color,scale,onHover,onSelect,emphasis=false,opacity=1}) {
  return <Shape stroke={color} fill={p.kind==='hatch'||p.kind==='text'?color:undefined}
    opacity={opacity} strokeWidth={(emphasis?2.7:1.2)/scale} hitStrokeWidth={10/scale}
    perfectDrawEnabled={false}
    sceneFunc={context=>paint(context._context,p,color,(emphasis?2.7:1.2)/scale)}
    hitFunc={(context,shape)=>{paint(context._context,p,color,10/scale,true);context.fillStrokeShape(shape);}}
    onMouseEnter={()=>onHover?.(p)} onMouseLeave={()=>onHover?.(null)}
    onClick={event=>{event.cancelBubble=true;onSelect?.(p);}} onTap={event=>{event.cancelBubble=true;onSelect?.(p);}}/>;
});
