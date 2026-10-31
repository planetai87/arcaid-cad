import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import PlanCanvas, {layerColor} from './components/PlanCanvas.jsx';
import {boundsOf,geometrySignature} from './utils/geometry.js';

async function getJSON(url) {
  const response=await fetch(url,{cache:'no-store'});
  if(!response.ok) throw new Error('파일을 읽지 못했습니다 ('+response.status+')');
  return response.json();
}
const api=(kind,path)=>'/api/'+kind+'?path='+encodeURIComponent(path);
const EMPTY=[];
export default function App() {
  const [workspace,setWorkspace]=useState({specs:[],bases:[]}),[records,setRecords]=useState({});
  const [enabled,setEnabled]=useState(new Set()),[basePath,setBasePath]=useState(''),[baseData,setBaseData]=useState(null);
  const [hidden,setHidden]=useState(new Set()),[selection,setSelection]=useState(null),[fitToken,setFitToken]=useState(0);
  const [changed,setChanged]=useState(new Set()),[coordinate,setCoordinate]=useState({x:0,y:0});
  const [connection,setConnection]=useState('연결 중'),[error,setError]=useState('');
  const recordsRef=useRef({}),versions=useRef({}),changedRef=useRef(new Map()),baseRef=useRef(''),known=useRef(new Set()),alive=useRef(true),baseInitialized=useRef(false);
  const loadSpec=useCallback(async(path,highlight=false)=>{
    const version=(versions.current[path]||0)+1;versions.current[path]=version;
    try {
      const data=await getJSON(api('spec',path));
      if(!alive.current||versions.current[path]!==version) return;
      const old=recordsRef.current[path];
      data.prims=(data.prims||[]).map(p=>({...p,_file:path,_key:path+':'+p.oid,_bounds:boundsOf([p])}));
      if(highlight&&old) {
        const previous=new Map(old.prims.map(p=>[p.oid,geometrySignature(p)]));
        for(const p of data.prims) if(previous.get(p.oid)!==geometrySignature(p)) changedRef.current.set(p._key,Date.now()+3000);
        setChanged(new Set(changedRef.current.keys()));
      }
      recordsRef.current={...recordsRef.current,[path]:data};setRecords(recordsRef.current);
      if(!old) setFitToken(t=>t+1);
    } catch(e) {
      if(!alive.current||versions.current[path]!==version) return;
      recordsRef.current={...recordsRef.current,[path]:{prims:[],violations:[{message:e.message}],warnings:[]}};
      setRecords(recordsRef.current);
    }
  },[]);
  const loadBase=useCallback(async(path)=>{
    const version=(versions.current.base||0)+1;versions.current.base=version;
    if(!path) {setBaseData(null);return;}
    try {
      const data=await getJSON(api('base',path));
      if(!alive.current||versions.current.base!==version) return;
      data.prims=data.prims.map((p,i)=>({...p,_key:'base:'+path+':'+i,_bounds:boundsOf([p])}));
      setBaseData(data);setFitToken(t=>t+1);
    }catch(e){if(alive.current&&versions.current.base===version){setBaseData(null);setError(e.message);}}
  },[]);
  const refresh=useCallback(async()=>{
    try {
      const data=await getJSON('/api/workspace');if(!alive.current)return;
      const next=new Set(data.specs.map(row=>row.path));
      setEnabled(previous=>{const updated=new Set([...previous].filter(p=>next.has(p)));
        for(const path of next) if(!known.current.has(path))updated.add(path);return updated;});
      for(const path of next) if(!known.current.has(path))void loadSpec(path);
      known.current=next;setWorkspace(data);setError('');
      if(!baseInitialized.current) {
        baseInitialized.current=true;
        if(data.bases.some(row=>row.path==='@base.dxf')) {
          baseRef.current='@base.dxf';setBasePath('@base.dxf');void loadBase('@base.dxf');
        }
      }
    } catch(e){if(alive.current)setError(e.message);}
  },[loadSpec,loadBase]);
  useEffect(()=>{
    alive.current=true;void refresh();
    const events=new EventSource('/api/events');
    events.onopen=()=>{setConnection('변경 감지 중');void refresh();
      // 재연결 사이 놓친 변경도 다시 읽는다.
      for(const path of known.current)void loadSpec(path,true);
      if(baseRef.current)void loadBase(baseRef.current);};
    events.onerror=()=>setConnection('다시 연결 중');
    events.onmessage=e=>{const event=JSON.parse(e.data);if(event.type!=='changed')return;
      void refresh();
      if(event.path.toLowerCase().endsWith('.json')&&known.current.has(event.path))void loadSpec(event.path,true);
      if(event.path===baseRef.current)void loadBase(event.path);
    };
    const timer=setInterval(()=>{
      let expired=false;for(const [key,until]of changedRef.current)if(until<=Date.now()){changedRef.current.delete(key);expired=true;}
      if(expired)setChanged(new Set(changedRef.current.keys()));
    },250);
    return ()=>{alive.current=false;events.close();clearInterval(timer);};
  },[refresh,loadSpec,loadBase]);
  const prims=useMemo(()=>workspace.specs.filter(row=>enabled.has(row.path)).flatMap(row=>records[row.path]?.prims||[]),[workspace,enabled,records]);
  const base=baseData?.prims||EMPTY;
  const layers=useMemo(()=>Object.assign({},...Object.values(records).map(r=>r.layers||{})),[records]);
  const layerNames=useMemo(()=>[...new Set([...prims,...base].map(p=>p.layer))].sort(),[prims,base]);
  const grids=useMemo(()=>workspace.specs.filter(row=>enabled.has(row.path)).map(row=>({path:row.path,grid:records[row.path]?.grid})),[workspace,enabled,records]);
  const selected=useMemo(()=>selection?[...prims,...base].find(p=>p._key===selection):null,[selection,prims,base]);
  const selectObject=useCallback(p=>setSelection(p?._key||null),[]);
  const item=selected?.sources?.[0]?.item;
  const instances=item?prims.filter(p=>p._file===selected._file&&p.sources?.[0]?.item===item).length:0;
  const issues=workspace.specs.filter(row=>enabled.has(row.path)).flatMap(row=>[
    ...(records[row.path]?.violations||[]).map(v=>({path:row.path,message:v.message,error:true})),
    ...(records[row.path]?.warnings||[]).map(message=>({path:row.path,message}))]);
  for(const message of baseData?.warnings||[])issues.push({path:basePath,message});
  const toggle=(setter,value)=>setter(previous=>{const result=new Set(previous);result.has(value)?result.delete(value):result.add(value);return result;});
  const chooseBase=e=>{const path=e.target.value;baseRef.current=path;setBasePath(path);setSelection(null);void loadBase(path);};
  return <div className="app">
    <header className="topbar"><div className="brand">arcaid <span>도면 검토</span></div><div className="mode">읽기 전용</div><span className="connection" role="status"><i className={connection==='변경 감지 중'?'online':''}/>{connection}</span></header>
    <div className="workspace">
      <aside className="files panel" aria-label="파일과 레이어">
        <section><div className="section-title"><h2>작업 명세</h2><span>{workspace.specs.length}개</span></div>
          <p className="hint">함께 볼 명세를 켜세요.</p>
          {!workspace.specs.length&&<p className="empty">작업 폴더에 명세 JSON이 없습니다.</p>}
          <div className="file-list">{workspace.specs.map(row=><label key={row.path} className={'file-row '+(enabled.has(row.path)?'enabled':'')}>
            <input type="checkbox" checked={enabled.has(row.path)} onChange={()=>toggle(setEnabled,row.path)}/>
            <span><strong>{row.op||row.path.split('/').at(-1)}</strong><small>{row.path}</small>{(row.error||records[row.path]?.violations?.length>0)&&<em>명세 오류 · 상세 확인</em>}</span>
          </label>)}</div>
        </section>
        <section><h2>기준 도면</h2><label className="sr-only" htmlFor="base-select">기준 DXF 선택</label>
          <select id="base-select" value={basePath} onChange={chooseBase}><option value="">기준 도면 없이 보기</option>{workspace.bases.map(row=><option key={row.path} value={row.path}>{row.name||row.path}</option>)}</select>
          <p className="hint">기준 객체는 회색으로 표시합니다.</p>{baseData?.truncated&&<p className="error">기준 도면의 일부만 표시 중입니다.</p>}
        </section>
        <section><div className="section-title"><h2>레이어</h2><span>{layerNames.length}개</span></div>
          <div className="layer-list">{layerNames.map(name=><label key={name}><input type="checkbox" checked={!hidden.has(name)} onChange={()=>toggle(setHidden,name)}/><i style={{background:layerColor(layers[name]?.color_rgb)}}/>{name}</label>)}</div>
        </section>
        <div className="files-footer">원본 파일이 바뀌면 화면에 반영됩니다.</div>
      </aside>
      <main className="drawing"><div className="canvas-toolbar"><div><strong>모형 공간</strong><span>{prims.length.toLocaleString()}개 객체{base.length>0?' + 기준 '+base.length.toLocaleString()+'개':''}</span></div><button onClick={()=>setFitToken(t=>t+1)}>화면 맞춤</button></div>
        {error&&<div className="error-banner" role="alert">{error}<button onClick={refresh}>다시 읽기</button></div>}
        <PlanCanvas prims={prims} base={base} grids={grids} layers={layers} hidden={hidden} selected={selected} onSelect={selectObject} changed={changed} fitToken={fitToken} onCoordinate={setCoordinate}/>
        <footer className="statusbar"><span>Y ↑ · mm</span><output>X {coordinate.x.toLocaleString()}　Y {coordinate.y.toLocaleString()}</output><span>{changed.size?'변경 객체를 3초간 강조합니다':'원본 좌표 그대로 표시'}</span></footer>
      </main>
      <aside className="details panel" aria-label="객체 정보"><section><div className="section-title"><h2>객체 정보</h2>{selected&&<button className="text-button" onClick={()=>setSelection(null)}>선택 해제</button>}</div>
        {!selected?<div className="selection-empty"><div className="crosshair">⌖</div><strong>객체를 클릭해 보세요</strong><p>원본 항목과 반복 배치 위치,<br/>레이어와 좌표를 확인할 수 있습니다.</p></div>:<>
          <div className="object-kind">{selected.kind}<span>{selected.handle?'기준 DXF':'작업 명세'}</span></div>
          <dl>{['layer','oid','path','handle'].filter(key=>selected[key]!=null).map(key=><React.Fragment key={key}><dt>{key}</dt><dd>{selected[key]||'—'}</dd></React.Fragment>)}</dl>
          {item&&<><div className="instance-count">이 항목의 인스턴스 {instances}개</div><h3>원본 위치 · sources</h3>{selected.sources.map((source,i)=><div className="source" key={i}><code>{source.item}</code>{source.groups.map((group,j)=><p key={j}>{group.group}<br/><span>place {group.place} · 반복 {group.i}</span></p>)}</div>)}</>}
          <h3>주요 좌표 · mm</h3><pre>{JSON.stringify(Object.fromEntries(['points','bulges','center','radius','start_angle','end_angle','at','text','height','rotation','align','name','loops','angle'].filter(key=>selected[key]!=null&&(!Array.isArray(selected[key])||selected[key].length)).map(key=>[key,selected[key]])),null,2)}</pre>
        </>}
      </section>{issues.length>0&&<section className="issues"><h2>확인할 내용 <span>{issues.length}</span></h2>{issues.map((issue,i)=><div className={issue.error?'issue invalid':'issue'} key={i}><small>{issue.path}</small><p>{issue.message}</p></div>)}</section>}
      </aside>
    </div>
  </div>;
}
