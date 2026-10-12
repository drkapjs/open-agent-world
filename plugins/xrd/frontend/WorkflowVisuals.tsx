import {useEffect,useRef,type CSSProperties} from 'react';
import {ArrowLeft,ArrowRight,Play,Pause,RotateCcw,Square,Link2} from '@oaw/plugin-api';
import type {ScientificFrame} from './FrameCanvas';
export const actionIcons={back:ArrowLeft,next:ArrowRight,play:Play,pause:Pause,restart:RotateCcw,stop:Square,follow:Link2};
export function ActionIcon({name}:{name:keyof typeof actionIcons}){const Icon=actionIcons[name];return <Icon size={18} strokeWidth={1.7} aria-hidden="true"/>;}
export function frameScore(frame:ScientificFrame){return typeof frame.quality==='number'&&Number.isFinite(frame.quality)&&frame.quality>=0&&frame.quality<=1?frame.quality:undefined;}
export function frameDescription(frame:ScientificFrame,index:number){const q=frameScore(frame);return `第 ${index+1} 帧 · ${q===undefined?'未评分':`质量分 ${(q*100).toFixed(3)} / 100`}${frame.metric&&frame.metric.value!=null?` · ${frame.metric.name} ${frame.metric.value} ${frame.metric.unit}`:''} · 标准卡片：${frame.label || frame.candidate_id}`;}
export function ScoreBars({frames,index,onSelect,phases,loading=false}:{loading?:boolean;frames:ScientificFrame[];phases?:Record<string,string>;index:number;onSelect:(index:number)=>void}){
 const root=useRef<HTMLDivElement>(null);
 useEffect(()=>{const reveal=()=>{const el=root.current?.querySelector<HTMLElement>('[aria-pressed="true"]');if(el&&root.current){const parent=root.current;const left=el.offsetLeft;if(left<parent.scrollLeft)parent.scrollLeft=left;else if(left+el.offsetWidth>parent.scrollLeft+parent.clientWidth)parent.scrollLeft=left+el.offsetWidth-parent.clientWidth;}};reveal();if(typeof ResizeObserver==='undefined'||!root.current)return;const observer=new ResizeObserver(reveal);observer.observe(root.current);return()=>observer.disconnect();},[index,frames.length]);
 return <div ref={root} className="xrd-score-scroll nowheel" role="group" aria-label="按帧质量分切换">{!frames.length?<span className="xrd-score-empty">尚无帧</span>:frames.map((f,i)=>{const q=frameScore(f);const description=frameDescription(f,i);return <button key={i} type="button" className={`xrd-score-hit ${q===undefined?'is-unrated':q===0?'is-zero':''} ${loading&&i===index?'is-loading':''}`} aria-busy={loading&&i===index} aria-label={description} aria-pressed={index===i} title={description} onClick={()=>onSelect(i)} onKeyDown={e=>{let next:number|undefined;if(e.key==='ArrowLeft')next=Math.max(0,i-1);if(e.key==='ArrowRight')next=Math.min(frames.length-1,i+1);if(e.key==='Home')next=0;if(e.key==='End')next=frames.length-1;if(next!==undefined){e.preventDefault();onSelect(next);(root.current?.children[next] as HTMLElement)?.focus();}}}>
   {phases&&<span className="xrd-score-phase">{phases[f.candidate_id]||'未提供物相'}</span>}<span className="xrd-score-well"><span className="xrd-score-column" style={{height:q===undefined?'28px':`${q*116}px`,'--xrd-score-hue':q===undefined?undefined:q*120} as CSSProperties}/></span><span className="xrd-score-number">{String(i+1).padStart(2,'0')}</span><span className="xrd-score-tooltip" role="tooltip">{description}</span>
 </button>;})}</div>;
}
export function ActivityStatus({active,status,label}:{active:boolean;status:string;label:string}){
 const text=active?'运行中':status==='failed'?'失败':status==='cancelled'?'已取消':status==='completed'||status==='succeeded'?'已完成':'待运行';
 return <span className="xrd-step-status" data-state={active?'running':status} role="status" aria-label={`${label}：${text}（任务活动指示）`} title={`${label}：${text}`}>{text==='待运行'?null:text}</span>;
}
