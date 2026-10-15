// @vitest-environment jsdom
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {it,expect,vi,afterEach} from 'vitest';
import {ScoreBars,ActivityStatus} from '../../../plugins/xrd/frontend/WorkflowVisuals';
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const f=(quality?:number)=>({candidate_id:'c',label:'C',state:'initial',quality});
it('keeps fixed heights for equal scores, zero and missing, independently of selection',()=>{
 const frames=[f(0),f(.5),f(.5),f(),f(1)];const select=vi.fn();
 const {container,rerender}=render(<ScoreBars frames={frames} index={1} onSelect={select}/>);
 const heights=()=>Array.from(container.querySelectorAll<HTMLElement>('.xrd-score-column')).map(n=>n.style.height);
 expect(heights()).toEqual(['0px','58px','58px','28px','116px']);
 fireEvent.click(screen.getByRole('button',{name:/第 3 帧/}));expect(select).toHaveBeenCalledWith(2);
 rerender(<ScoreBars frames={frames} index={2} onSelect={select}/>);expect(heights()).toEqual(['0px','58px','58px','28px','116px']);
 expect(screen.getByRole('button',{name:/第 4 帧 · 未评分/})).toBeTruthy();
 fireEvent.keyDown(screen.getByRole('button',{name:/第 3 帧/}),{key:'End'});expect(select).toHaveBeenLastCalledWith(4);
 rerender(<ScoreBars frames={[]} index={0} onSelect={select}/>);expect(screen.getByText('尚无帧')).toBeTruthy();
 rerender(<ScoreBars frames={[f(.3)]} index={0} onSelect={select}/>);expect(screen.getAllByRole('button')).toHaveLength(1);
});
it('announces workflow status and keeps failure and cancellation visible',()=>{
 const {rerender}=render(<ActivityStatus active status="running" label="预优化"/>);
 expect(screen.getByRole('status',{name:'预优化：运行中（任务活动指示）'}).textContent).toBe('运行中');
 for(const [status,text] of [['failed','失败'],['cancelled','已取消'],['completed','已完成']]){
   rerender(<ActivityStatus active={false} status={status} label="预优化"/>);
   const indicator=screen.getByRole('status',{name:`预优化：${text}（任务活动指示）`});
   expect(indicator.getAttribute('data-state')).toBe(status);expect(indicator.textContent).toBe(text);
 }
 rerender(<ActivityStatus active={false} status="idle" label="预优化"/>);
 expect(screen.getByRole('status',{name:'预优化：待运行（任务活动指示）'})).toBeTruthy();
});
it('scrolls a long frame list to the selected item without resizing or reordering bars',()=>{
 const frames=Array.from({length:50},(_,i)=>f(i/50));const select=vi.fn();
 const {container,rerender}=render(<ScoreBars frames={frames} index={0} onSelect={select}/>);
 const root=container.querySelector<HTMLElement>('.xrd-score-scroll')!;
 Object.defineProperty(root,'clientWidth',{value:180});
 const buttons=Array.from(root.children) as HTMLElement[];
 buttons.forEach((button,i)=>{Object.defineProperty(button,'offsetLeft',{value:i*68});Object.defineProperty(button,'offsetWidth',{value:54});});
 rerender(<ScoreBars frames={frames} index={49} onSelect={select}/>);
 expect(root.scrollLeft).toBe(49*68+54-180);
 rerender(<ScoreBars frames={frames} index={0} onSelect={select}/>);expect(root.scrollLeft).toBe(0);
 expect(root.children).toHaveLength(50);expect(buttons[49].getAttribute('aria-label')).toContain('98.000');
});
