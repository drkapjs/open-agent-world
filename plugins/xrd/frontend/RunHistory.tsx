import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { PluginViewProps } from '@oaw/plugin-api';
import './history.css';

type Run = {run_id:string; workflow_stage:string; status:string; created_at_ns:number; finished_at_ns?:number};
type Detail = {manifest:Run; parameters:Record<string,unknown>; files:{name:string;size_bytes:number}[]};
const stages:Record<string,string>={search:'谱库检索',preopt:'结构预优化',fit:'单相全谱拟合',legacy_fit:'全谱拟合',multiphase:'多相筛选与联合复核'};
const statuses:Record<string,string>={completed:'已完成',running:'运行中',failed:'失败',cancelled:'已停止',interrupted:'已中断'};

export function RunHistory({host}:{host:PluginViewProps['host']}) {
  const [open,setOpen]=useState(false);
  return <><button type="button" className="xrd-run-history" onClick={()=>setOpen(true)} aria-label="运行历史" title="运行历史"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7"/><path d="M12 7v5l3 2"/></svg></button>
    {open&&createPortal(<HistoryDialog host={host} close={()=>setOpen(false)}/>,document.body)}</>;
}

function HistoryDialog({host,close}:{host:PluginViewProps['host'];close:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const request=useRef(0);
  const [items,setItems]=useState<Run[]>([]),[offset,setOffset]=useState(0),[total,setTotal]=useState(0);
  const [detail,setDetail]=useState<Detail>(),[preview,setPreview]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{dialog.current?.showModal();return()=>{request.current++;};},[]);
  useEffect(()=>{let alive=true;setBusy(true);setError('');
    host.resourceAction('history_list',{offset}).then(value=>{if(alive){setItems(value.items as Run[]);setTotal(Number(value.total));}})
      .catch(e=>{if(alive)setError(String(e));}).finally(()=>{if(alive)setBusy(false);});
    return()=>{alive=false;};
  },[host,offset]);
  const inspect=async(run:Run)=>{
    const id=++request.current;setBusy(true);setError('');setDetail(undefined);setPreview('');
    try{const value=await host.resourceAction('history_inspect',{run_id:run.run_id});if(id===request.current)setDetail(value as unknown as Detail);}
    catch(e){if(id===request.current)setError(String(e));}finally{if(id===request.current)setBusy(false);}
  };
  const file=async(name:string,download=false)=>{
    if(!detail)return;const id=++request.current;setBusy(true);setError('');setPreview('');
    try{
      const value=await host.resourceAction('history_file',{run_id:detail.manifest.run_id,name});if(id!==request.current)return;
      const bytes=Uint8Array.from(atob(String(value.data)),c=>c.charCodeAt(0));
      if(download){const url=URL.createObjectURL(new Blob([bytes]));const link=document.createElement('a');link.href=url;link.download=name.split('/').pop()!;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      else{const text=new TextDecoder().decode(bytes);setPreview(text.length>150000?text.slice(0,150000)+'\n…内容较长，请下载完整文件。':text);}
    }catch(e){if(id===request.current)setError(String(e));}finally{if(id===request.current)setBusy(false);}
  };
  const fileRow=(item:{name:string;size_bytes:number})=><li key={item.name}><button disabled={busy} onClick={()=>void file(item.name)}>{({'result.json':'计算结果','multiphase-state.json':'多相结果与状态','failure.json':'失败记录','input-snapshot.json':'输入快照'} as Record<string,string>)[item.name]??item.name}</button><small>{(item.size_bytes/1024).toFixed(1)} KiB</small><button disabled={busy} onClick={()=>void file(item.name,true)}>下载</button></li>;
  return <dialog ref={dialog} className="xrd-history-dialog nodrag" aria-label="XRD 运行历史" onCancel={e=>{e.preventDefault();close();}}>
    <header><strong>运行历史</strong><button onClick={close} aria-label="关闭运行历史">×</button></header>
    <div className="xrd-history-layout"><aside>
      {!busy&&!items.length&&<p>尚无运行记录</p>}
      {items.map(run=><button key={run.run_id} className={detail?.manifest.run_id===run.run_id?'is-selected':''} onClick={()=>void inspect(run)}>
        <strong>{stages[run.workflow_stage]??run.workflow_stage}</strong><span>{statuses[run.status]??run.status}</span>
        <small>{new Date(run.created_at_ns/1e6).toLocaleString()} · {run.run_id.slice(0,8)}</small>
      </button>)}
      <nav><button disabled={busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>上一页</button><span>{total} 次运行</span><button disabled={busy||offset+50>=total} onClick={()=>setOffset(offset+50)}>下一页</button></nav>
    </aside><section>
      {busy&&<p role="status">正在读取…</p>}{error&&<p role="alert">{error}</p>}
      {detail?<><h3>{stages[detail.manifest.workflow_stage]??detail.manifest.workflow_stage} · {statuses[detail.manifest.status]??detail.manifest.status}</h3>
        <small>{detail.manifest.run_id}</small>
        <details><summary>运行参数与来源</summary><pre>{JSON.stringify({manifest:detail.manifest,parameters:detail.parameters},null,2)}</pre></details>
        <ul>{detail.files.filter(item=>['result.json','multiphase-state.json','failure.json','input-snapshot.json'].includes(item.name)).map(fileRow)}</ul>
        {preview&&<pre aria-label="历史文件内容">{preview}</pre>}
        <details><summary>其他文件</summary><ul>{detail.files.filter(item=>!['result.json','multiphase-state.json','failure.json','input-snapshot.json'].includes(item.name)).map(fileRow)}</ul></details>
      </>:!busy&&<p>选择一次运行，查看已保存的结果、参数和状态。</p>}
    </section></div>
  </dialog>;
}
