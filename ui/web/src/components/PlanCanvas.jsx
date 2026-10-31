import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Stage, Layer, Group, Shape, Line, Text } from 'react-konva';
import Primitive, { paint } from './Primitive.jsx';
import { boundsOf, hitDistance, visibleIn } from '../utils/geometry.js';
import { fromScreen } from '../utils/coords.js';
import { fitView, zoomAt } from '../utils/viewport.js';
import { snapToGrid } from '../utils/snap.js';

const StaticBase=memo(function StaticBase({prims,bounds,scale}) {
  const ref=useRef(null);
  useEffect(()=>{
    if(!prims.length||!ref.current) return;
    const width=Math.max(1,bounds.maxX-bounds.minX),height=Math.max(1,bounds.maxY-bounds.minY);
    ref.current.cache({x:bounds.minX-20/scale,y:-bounds.maxY-20/scale,width:width+40/scale,
      height:height+40/scale,pixelRatio:Math.min(scale,4096/Math.max(width+40/scale,height+40/scale)),
      hitCanvasPixelRatio:0.01});
    ref.current.getLayer()?.batchDraw();
    return ()=>ref.current?.clearCache();
  },[prims,bounds,scale]);
  return <Group ref={ref} listening={false}><Shape listening={false} sceneFunc={ctx=>{
    for(const p of prims) paint(ctx._context,p,'#a6b4bf',1/scale);
  }}/></Group>;
});
export function layerColor(rgb=[220,230,238]) {
  // 어두운 배경에서 검정·짙은 ACI 색도 구분되게 밝기를 보정한다.
  const max=Math.max(...rgb),factor=max<145?145/Math.max(max,1):1;
  return 'rgb('+rgb.map(n=>Math.max(45,Math.round(n*factor))).join(',')+')';
}
export default function PlanCanvas({prims,base,grids,layers,hidden,selected,onSelect,changed,fitToken,onCoordinate}) {
  const container=useRef(null),stage=useRef(null);
  const [size,setSize]=useState({width:800,height:600}),[view,setView]=useState({x:40,y:500,scale:0.03});
  const [hover,setHover]=useState(null),[pointer,setPointer]=useState({x:0,y:0});
  const frame=useRef(null),latest=useRef(null),previousFit=useRef('');
  useEffect(()=>{
    const observer=new ResizeObserver(entries=>{const r=entries[0].contentRect;setSize({width:r.width,height:r.height});});
    observer.observe(container.current);return ()=>{observer.disconnect();cancelAnimationFrame(frame.current);};
  },[]);
  const all=useMemo(()=>[...base,...prims], [base,prims]);
  const bounds=useMemo(()=>boundsOf(all),[all]);
  const baseBounds=useMemo(()=>boundsOf(base),[base]);
  useEffect(()=>{
    const identity=fitToken+':'+size.width+':'+size.height;
    if(previousFit.current!==identity && all.length) {setView(fitView(bounds,size));previousFit.current=identity;}
  },[fitToken,size,bounds,all]);
  const visible=useMemo(()=>prims.filter(p=>!hidden.has(p.layer)&&visibleIn(p,view,size)),[prims,hidden,view,size]);
  const baseVisible=useMemo(()=>base.filter(p=>!hidden.has(p.layer)),[base,hidden]);
  const source=selected?.sources?.[0]?.item;
  const wheel=e=>{e.evt.preventDefault();const point=e.target.getStage().getPointerPosition();
    setView(v=>zoomAt(v,point,Math.pow(1.0015,-e.evt.deltaY)));};
  const move=e=>{
    latest.current=e.target.getStage().getPointerPosition();
    if(frame.current) return;
    frame.current=requestAnimationFrame(()=>{const point=latest.current;frame.current=null;if(!point)return;
      setPointer(point);const mm=fromScreen(point,view);onCoordinate({x:snapToGrid(mm.x),y:snapToGrid(mm.y)});});
  };
  const pickBase=e=>{
    if(e.target!==e.target.getStage()) return;
    const point=fromScreen(e.target.getStage().getPointerPosition(),view);
    let best=null,distance=8/view.scale;
    for(const p of baseVisible) {const d=hitDistance(p,[point.x,point.y]);if(d<distance){best=p;distance=d;}}
    onSelect(best);
  };
  return <div className="canvas" ref={container}>
    <Stage ref={stage} width={size.width} height={size.height} x={view.x} y={view.y} scaleX={view.scale} scaleY={view.scale}
      draggable onWheel={wheel} onMouseMove={move} onMouseLeave={()=>setHover(null)} onClick={pickBase} onTap={pickBase}
      onDragEnd={e=>{if(e.target===e.target.getStage()) setView(v=>({...v,x:e.target.x(),y:e.target.y()}));}}>
      <Layer listening={false}><StaticBase prims={baseVisible} bounds={baseBounds} scale={view.scale}/></Layer>
      <Layer listening={false}>{grids.flatMap(({grid,path},index)=>[
        ...Object.entries(grid?.X||{}).flatMap(([name,x])=>[
          <Line key={path+'x'+name} points={[x,-bounds.minY,x,-bounds.maxY]} stroke="#567080" opacity={0.4} strokeWidth={0.7/view.scale} dash={[5/view.scale,6/view.scale]}/>,
          <Text key={path+'xt'+name} x={x} y={-bounds.maxY-20/view.scale-index*20/view.scale} text={name} fontSize={15/view.scale} fill="#a8bfce"/>]),
        ...Object.entries(grid?.Y||{}).flatMap(([name,y])=>[
          <Line key={path+'y'+name} points={[bounds.minX,-y,bounds.maxX,-y]} stroke="#567080" opacity={0.4} strokeWidth={0.7/view.scale} dash={[5/view.scale,6/view.scale]}/>,
          <Text key={path+'yt'+name} x={bounds.minX-40/view.scale-index*26/view.scale} y={-y} text={name} fontSize={15/view.scale} fill="#a8bfce"/>])])}</Layer>
      <Layer>{visible.map(p=>{
        const same=source && p._file===selected?._file && p.sources?.[0]?.item===source;
        const active=p._key===selected?._key||p._key===hover?._key;
        const recent=changed.has(p._key);
        return <Primitive key={p._key} p={p} scale={view.scale} color={active?'#ffffff':recent?'#ffcb75':same?'#ffd84d':layerColor(layers[p.layer]?.color_rgb)}
          emphasis={active||recent||same} opacity={1} onHover={setHover} onSelect={onSelect}/>;
      })}</Layer>
      <Layer listening={false}>{selected?.handle && <Primitive p={selected} color="#ffffff" scale={view.scale} emphasis/>}</Layer>
    </Stage>
    {!all.length&&<div className="canvas-empty"><strong>검토할 도면을 선택하세요</strong><span>왼쪽에서 명세를 켜거나 기준 DXF를 고르세요.</span></div>}
    {hover&&<div className="tooltip" style={{left:Math.min(pointer.x+16,size.width-255),top:Math.min(pointer.y+18,size.height-90)}}>
      <strong>{hover.oid}</strong><span>{hover.path||'경로 없음'}</span><span>레이어 {hover.layer}</span></div>}
    <div className="canvas-guide">휠로 확대 · 드래그로 이동 · 클릭으로 객체 확인</div>
  </div>;
}
