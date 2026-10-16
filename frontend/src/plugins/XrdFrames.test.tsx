import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SpectrumCanvas, FrameTimeline, selectedFrame, qualityColor, type FrameSet } from '../../../plugins/xrd/frontend/FrameCanvas';
import type { PluginViewProps } from './sdk';
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks();});
const host={openLinkedCanvas:vi.fn().mockResolvedValue(undefined)} as unknown as PluginViewProps['host'];
const data:FrameSet={run_id:'test',stage:'preopt',observed:[[10,1],[20,2]],frames:[{candidate_id:'a',label:'A',state:'initial'},{candidate_id:'a',label:'A',state:'iteration',quality:.5}]};
it('pins the shared cursor while new live frames append, then follows explicitly',()=>{
  const {rerender}=render(<FrameTimeline source="test-pin" data={data} host={host}/>);
  fireEvent.click(screen.getByRole('button',{name:/第 1 帧/}));
  rerender(<FrameTimeline source="test-pin" data={{...data,frames:[...data.frames,{candidate_id:'b',label:'B',state:'final'}]}} host={host}/>);
  expect(selectedFrame('test-pin')?.candidate_id).toBe('a');
  fireEvent.click(screen.getByRole('button',{name:'跟随实时'}));
  expect(selectedFrame('test-pin')?.candidate_id).toBe('b');
});
it('search starts at first ranked candidate and run changes do not reuse an old cursor',()=>{
  const {rerender}=render(<FrameTimeline source="test-run" data={data} host={host}/>);
  rerender(<FrameTimeline source="test-run" data={{...data,run_id:'search2',stage:'search',frames:[{candidate_id:'c',label:'C',state:'initial'},...data.frames]}} host={host}/>);
  expect(selectedFrame('test-run')?.candidate_id).toBe('c');
});
it('unknown quality is neutral rather than misleading red or green',()=>{
  expect(qualityColor(undefined)).toBe('#77766d');
  expect(qualityColor(0)).toContain('hsl(0 ');
  expect(qualityColor(1)).toContain('hsl(120 ');
});
// @vitest-environment jsdom

it('opens the same spectrum in a modal and restores focus on close',()=>{
  const show=vi.fn(function(this:HTMLDialogElement){this.setAttribute('open','');});
  Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:show});
  render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]}/>);
  const before=screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent;
  fireEvent.click(screen.getByRole('button',{name:'放大谱图'}));
  expect(show).toHaveBeenCalledOnce();
  expect(screen.getByRole('dialog',{name:'放大 XRD 谱图'})).toBeTruthy();
  expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent).toBe(before);
  fireEvent.click(screen.getByRole('button',{name:'关闭放大谱图'}));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole('button',{name:'放大谱图'}));
});

it('pans the spectrum and shares line visibility with the enlarged view',()=>{
 vi.stubGlobal('PointerEvent',MouseEvent);
 const {container}=render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]} frame={{candidate_id:'a',label:'A',state:'final',calculated:[[10,1],[20,1],[30,1]]}}/>);
 const svg=screen.getByRole('img',{name:'同步 XRD 谱画布'});
 svg.getBoundingClientRect=()=>({left:0,width:800,height:320,top:0,right:800,bottom:320,x:0,y:0,toJSON(){}});
 Object.assign(svg,{setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()});
 const ticks=svg.textContent;
 fireEvent.pointerDown(svg,{button:0,clientX:400});
 fireEvent.pointerMove(svg,{clientX:471});
 fireEvent.pointerUp(svg,{clientX:471});
 expect(svg.textContent).not.toBe(ticks);
 expect(svg.textContent).toContain('8.00');
 fireEvent.click(screen.getByRole('button',{name:'放大谱图'}));
 fireEvent.click(screen.getByRole('switch',{name:'实验谱'}));
 expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).querySelector('polyline[stroke="#b9d8dd"]')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'关闭放大谱图'}));
 expect(container.querySelector('polyline[stroke="#b9d8dd"]')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'复位范围'}));
 expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent).toBe(ticks);
 vi.unstubAllGlobals();
});

it('keeps readable local coordinates and accurate picking after a narrow resize at world zoom',()=>{
 let resize!:ResizeObserverCallback;
 const disconnect=vi.fn();
 vi.stubGlobal('ResizeObserver',class {
   constructor(callback:ResizeObserverCallback){resize=callback;}
   observe=vi.fn();disconnect=disconnect;
 });
 vi.stubGlobal('PointerEvent',MouseEvent);
 const {unmount}=render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]}/>);
 const svg=screen.getByRole('img',{name:'同步 XRD 谱画布'});
 act(()=>resize([{contentRect:{width:320,height:480}}] as ResizeObserverEntry[],{} as ResizeObserver));
 expect(svg.getAttribute('viewBox')).toBe('0 0 320 480');
 expect(svg.querySelectorAll('text')).toHaveLength(4);
 expect(svg.querySelector('text')?.getAttribute('font-size')).toBe('12');
 act(()=>resize([{contentRect:{width:0,height:0}}] as ResizeObserverEntry[],{} as ResizeObserver));
 expect(svg.getAttribute('viewBox')).toBe('0 0 320 480');
 svg.getBoundingClientRect=()=>({left:100,width:160,height:240,top:100,right:260,bottom:340,x:100,y:100,toJSON(){}});
 Object.assign(svg,{setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()});
 fireEvent.pointerMove(svg,{clientX:180});
 expect(svg.textContent).toContain('2θ 20.0000° · I 2.00');
 fireEvent.pointerDown(svg,{button:0,clientX:180});
 fireEvent.pointerMove(svg,{clientX:191.5});
 fireEvent.pointerUp(svg,{clientX:191.5});
 expect(svg.textContent).toContain('8.00');
 unmount();expect(disconnect).toHaveBeenCalledOnce();
});

it('bounds portrait PNG dimensions and snapshots the theme before asynchronous decoding',async()=>{
 vi.useFakeTimers();
 let resize!:ResizeObserverCallback;
 vi.stubGlobal('ResizeObserver',class {constructor(callback:ResizeObserverCallback){resize=callback;}observe(){}disconnect(){}});
 let finishDecode!:()=>void;
 vi.stubGlobal('Image',class {src='';decode=()=>new Promise<void>(resolve=>{finishDecode=resolve;});});
 const createObjectURL=vi.fn(()=> 'blob:spectrum');
 vi.stubGlobal('URL',{createObjectURL,revokeObjectURL:vi.fn()});
 const context={fillStyle:'',fillRect:vi.fn(),drawImage:vi.fn()};
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
 let dimensions:number[]=[];
 vi.spyOn(HTMLCanvasElement.prototype,'toBlob').mockImplementation(function(this:HTMLCanvasElement,callback){dimensions=[this.width,this.height];callback(new Blob());});
 vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 const {container}=render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]}/>);
 act(()=>resize([{contentRect:{width:120,height:1000}}] as ResizeObserverEntry[],{} as ResizeObserver));
 const surface=container.querySelector<HTMLElement>('.xrd-unified-spectrum')!;
 surface.style.backgroundColor='rgb(30, 25, 20)';
 fireEvent.click(screen.getByRole('button',{name:'保存当前谱图'}));
 surface.style.backgroundColor='rgb(245, 240, 235)';
 await act(async()=>{finishDecode();});
 expect(dimensions).toEqual([492,4096]);
 expect(context.fillStyle).toBe('rgb(30, 25, 20)');
 expect(context.drawImage).toHaveBeenCalledWith(expect.anything(),0,0,492,4096);
 await vi.advanceTimersByTimeAsync(1000);
});
