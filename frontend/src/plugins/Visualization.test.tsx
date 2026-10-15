// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {useMemo,useState} from 'react';
import type {PluginViewProps} from './sdk';
import {Visualization} from '../../../plugins/visualization/frontend';

vi.mock('../../../plugins/visualization/frontend/ChartCanvas',()=>({ChartCanvas:()=> <div data-testid="configured-chart"/>}));
afterEach(cleanup);

it.each(['focused keyboard control','change event without focus'])('keeps field editing open after the chart becomes configured: %s',async(interaction)=>{
  const updateConfig=vi.fn();
  const read=vi.fn(async()=>({kind:'table',columns:['step','temperature'],rows:[[1,22]]}));
  function Harness(){
    const [config,setConfig]=useState<Record<string,unknown>>({source_id:'database',schema_id:'readings',x:'step',y:''});
    const host=useMemo(()=>({
      listCards:async()=>[{id:'database',name:'Measurements'}],
      updateConfig:async(patch:Record<string,unknown>)=>{updateConfig(patch);setConfig(value=>({...value,...patch}));},
      dataSources:{
        list:async()=>({sources:[{id:'database',name:'Measurements'}]}),
        schemas:async()=>({schemas:[{id:'readings',label:'Readings',kind:'table',fields:[{name:'step',type:'number'},{name:'temperature',type:'number'}]}]}),
        read,subscribe:()=>()=>{},
      },
    }),[]);
    return <Visualization {...{card:{id:'chart',type:'data.visualization.line',config},host,level:'workspace'} as unknown as PluginViewProps}/>;
  }
  const view=render(<Harness/>);
  const y=await screen.findByRole('combobox',{name:'Y'});
  if(interaction==='focused keyboard control')fireEvent.focus(y);
  else expect(document.activeElement).not.toBe(y);
  fireEvent.change(y,{target:{value:'temperature'}});
  await screen.findByTestId('configured-chart');
  const toggle=view.container.querySelector('.viz-config-toggle')!;
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  fireEvent.change(screen.getByRole('combobox',{name:'Row limit'}),{target:{value:'10000'}});
  await waitFor(()=>expect(updateConfig).toHaveBeenLastCalledWith({limit:10000}));
  await waitFor(()=>expect(read).toHaveBeenLastCalledWith('database',expect.objectContaining({limit:10000})));
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByRole('combobox',{name:'Y'})).toBeNull();
});
