import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Focus,Maximize2,MousePointer2,Network,RotateCcw,Type} from 'lucide-react';
import '@falkordb/canvas';
const palette:any={Person:'#3b82f6',Team:'#60a5fa',Project:'#6366f1',Repository:'#6366f1',Module:'#0891b2',Function:'#0891b2',Class:'#d97706',Change:'#7c3aed',Ticket:'#e05b62',Source:'#0d9488',Decision:'#8b5cf6',Fact:'#d89a30',File:'#64748b'};
export default function GraphCanvas({graph,onSelect,search=''}:any){
 const ref=useRef<any>(null),wrap=useRef<HTMLDivElement>(null),root=useRef<HTMLDivElement>(null),onSelectRef=useRef(onSelect);onSelectRef.current=onSelect;
 const [layout,setLayout]=useState('force'),[expanded,setExpanded]=useState(false),[labels,setLabels]=useState(false),[focus,setFocus]=useState<string|null>(null),[hidden,setHidden]=useState<string[]>([]);
 useEffect(()=>{
  if(!expanded)return;
  const previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
  const keyboard=(event:KeyboardEvent)=>{
   if(event.key==='Escape')setExpanded(false);
   if(event.key==='Tab'){
    const controls=Array.from(root.current?.querySelectorAll<HTMLElement>('button,select,summary,a[href]')||[]).filter(el=>!el.hasAttribute('disabled')&&el.offsetParent!==null);
    const first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
   }
  };
  document.addEventListener('keydown',keyboard);
  return()=>{document.body.style.overflow=previousOverflow;document.removeEventListener('keydown',keyboard)};
 },[expanded]);
 const kinds=useMemo(()=>Array.from(new Set((graph.nodes||[]).map((n:any)=>n.kind))) as string[],[graph]);
 const shown=useMemo(()=>{
  const all=(graph.nodes||[]).filter((n:any)=>!hidden.includes(n.kind));
  const seeds=new Set(all.filter((n:any)=>search?`${n.title} ${n.kind} ${n.properties?.file_path||''}`.toLowerCase().includes(search.toLowerCase()):n.id===focus).map((n:any)=>n.id));
  const ids=new Set(seeds);
  if(search||focus)for(const e of graph.edges||[])if(seeds.has(e.source)||seeds.has(e.target)){ids.add(e.source);ids.add(e.target)}
  const nodes=search||focus?all.filter((n:any)=>ids.has(n.id)):all;
  const present=new Set(nodes.map((n:any)=>n.id));
  return {nodes,edges:(graph.edges||[]).filter((e:any)=>present.has(e.source)&&present.has(e.target))};
 },[graph,search,focus,hidden]);
 useEffect(()=>{setFocus(null);setHidden([])},[graph]);
 useEffect(()=>{
  const canvas=ref.current;if(!canvas||!wrap.current)return;
  const ids=new Map(shown.nodes.map((n:any,i:number)=>[n.id,i+1]));
  canvas.setData({nodes:shown.nodes.map((n:any,i:number)=>({id:i+1,labels:[n.kind],color:palette[n.kind]||'#64748b',visible:true,size:n.kind==='Repository'?15:n.kind==='Decision'?13:10,data:{...n,name:(n.title||n.id).slice(0,65)}})),links:shown.edges.map((e:any,i:number)=>({id:i+1,relationship:e.kind,color:'#a8b9c8',source:ids.get(e.source),target:ids.get(e.target),visible:true,data:e}))});
  const configure=()=>canvas.setConfig({width:wrap.current?.clientWidth||900,height:wrap.current?.clientHeight||580,backgroundColor:'#f8fbfd',foregroundColor:'#203348',layoutMode:layout,captionsKeys:['name'],node:{nodeCanvasObject:(n:any,ctx:CanvasRenderingContext2D)=>{const scale=Math.max(.1,ctx.getTransform().a/(window.devicePixelRatio||1)),radius=(n.labels?.includes('Repository')?8:n.labels?.includes('Decision')?7:5.5)/scale,name=(n.data?.name||'').slice(0,32);ctx.save();ctx.beginPath();ctx.arc(n.x||0,n.y||0,radius,0,Math.PI*2);ctx.fillStyle=n.color||'#64748b';ctx.fill();ctx.lineWidth=1.7/scale;ctx.strokeStyle='#ffffff';ctx.stroke();if(labels||shown.nodes.length<70||scale>.8){ctx.font=`500 ${11/scale}px "DM Sans",system-ui`;const x=(n.x||0)+radius+5/scale,y=(n.y||0)+3/scale;const width=ctx.measureText(name).width;ctx.fillStyle='rgba(255,255,255,.91)';ctx.fillRect(x-2/scale,y-10/scale,width+5/scale,14/scale);ctx.fillStyle='#425b78';ctx.fillText(name,x,y)}ctx.restore()},nodePointerAreaPaint:(n:any,color:string,ctx:CanvasRenderingContext2D)=>{const scale=Math.max(.1,ctx.getTransform().a/(window.devicePixelRatio||1));ctx.beginPath();ctx.arc(n.x||0,n.y||0,Math.max(7/scale,9),0,Math.PI*2);ctx.fillStyle=color;ctx.fill()}},nodeStyle:{fontFamily:'Inter, ui-sans-serif, system-ui',fontSize:11,textFillRatio:undefined,strokeWidthUnselected:.8},simulation:{chargeStrength:-650,centerStrength:.025,velocityDecay:.55,warmupTicks:180},largeGraph:{enabled:true,skipLabelsAtLowZoom:!labels},interaction:{zoomToFitPadding:.15,zoomToFitMaxZoom:3},eventHandlers:{onNodeClick:(node:any)=>{setExpanded(false);onSelectRef.current(node.data)}}});
  configure();const observer=new ResizeObserver(configure);observer.observe(wrap.current);
  const timer=setTimeout(()=>canvas.zoomToFit?.(1.2),350);
  return()=>{observer.disconnect();clearTimeout(timer)};
 },[shown,layout,labels]);
 return <div className={'graph-explorer'+(expanded?' expanded':'')} ref={root} role={expanded?'dialog':undefined} aria-modal={expanded||undefined} aria-label={expanded?'Knowledge graph':undefined}><div className="graph-controls"><div className="graph-mode"><Network size={15}/><select aria-label="Graph layout" value={layout} onChange={e=>setLayout(e.target.value)}><option value="force">Network layout</option><option value="tree">Hierarchy layout</option><option value="radial">Radial layout</option></select></div><div className="button-group"><button className={'button subtle '+(labels?'selected':'')} onClick={()=>setLabels(!labels)} title="Toggle automatic label visibility"><Type size={15}/>{labels?'Always show names':'Auto names'}</button><button className="button subtle" onClick={()=>ref.current?.zoomToFit?.(1.3)}><Focus size={15}/>Fit view</button><button className="button subtle" onClick={()=>setExpanded(!expanded)} aria-pressed={expanded}><Maximize2 size={15}/>{expanded?'Exit full screen':'Full screen'}</button>{focus&&<button className="button subtle" onClick={()=>setFocus(null)}><RotateCcw size={15}/>Show all</button>}</div></div><div className="graph-surface" ref={wrap}>{React.createElement('falkordb-canvas',{ref,style:{display:'block',width:'100%',height:'100%'}})}{!shown.nodes.length&&<div className="graph-empty"><Network size={36}/><h3>No matching connections</h3><p>Try another search or add a knowledge source.</p></div>}<div className="graph-floating-hint"><MousePointer2 size={13}/>Click a node to inspect · drag to arrange · scroll to zoom</div></div><div className="graph-bottom"><div className="graph-legend">{kinds.map(kind=><button key={kind} className={hidden.includes(kind)?'off':''} onClick={()=>setHidden(hidden.includes(kind)?hidden.filter(k=>k!==kind):[...hidden,kind])}><i style={{background:palette[kind]||'#64748b'}}/>{kind}<span>{graph.nodes.filter((n:any)=>n.kind===kind).length}</span></button>)}</div><span>{shown.nodes.length} nodes · {shown.edges.length} relationships</span></div><details className="graph-directory"><summary>Browse connected records</summary><div>{shown.nodes.slice(0,100).map((n:any)=><div key={n.id}><button onClick={()=>onSelect(n)}><i style={{background:palette[n.kind]||'#64748b'}}/>{n.title}</button><button className="icon-button" aria-label={'Focus connections for '+n.title} onClick={()=>setFocus(n.id)}><Focus size={15}/></button></div>)}</div></details></div>
}
