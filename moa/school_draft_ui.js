/* Private input snapshots only. No approval, localStorage, beacon or unload writes. */
(() => {
  'use strict';
  class AutosaveQueue {
    constructor({send,clock=globalThis,delay=1500}) {
      this.send=send; this.clock=clock; this.delay=delay;
      this.jobs=[]; this.active=null; this.paused=false;
    }
    add({read,saved,state}) {
      const job={read,saved,state,baseline:read().value,touched:false,ready:false,held:false,timer:null,failure:null};
      this.jobs.push(job); state('idle',{}); return job;
    }
    changed(job) {
      job.touched=true; this.clock.clearTimeout(job.timer); job.ready=false;
      if (job.failure) return job.state('error',job.failure.error);
      if (this.active===job) return;
      if (job.read().value===job.baseline) return job.state(job.last?'saved':'idle',job.last||{});
      job.state('waiting',{});
      if (!this.paused && !job.held) job.timer=this.clock.setTimeout(()=>{job.ready=true; this.pump();},this.delay);
    }
    hold(job,held) {job.held=held; this.changed(job);}
    retry(job) {
      if (!job.failure || job.failure.error.blocked || this.active===job) return;
      job.ready=true; this.pump();
    }
    pause() {
      this.paused=true;
      for (const job of this.jobs) {this.clock.clearTimeout(job.timer); job.ready=false;}
    }
    resume() {
      this.paused=false;
      for (const job of this.jobs) if (job.touched) this.changed(job);
      this.pump();
    }
    async pump() {
      if (this.active || this.paused) return;
      const job=this.jobs.find(j=>j.ready && !j.held && (!j.failure || !j.failure.error.blocked));
      if (!job) return;
      job.ready=false; this.active=job;
      const snapshot=job.failure?job.failure.snapshot:job.read();
      job.state('saving',{});
      try {
        const result=await this.send(snapshot.body);
        job.saved(snapshot,result); job.baseline=snapshot.value; job.last=result; job.failure=null;
      } catch (error) {
        job.failure={snapshot,error:{blocked:!!error.blocked,code:error.code||'network'}};
      }
      this.active=null;
      if (job.failure) job.state('error',job.failure.error);
      else this.changed(job); // Read again: never mark newer typing as acknowledged.
      this.pump();
    }
  }
  if (typeof module!=='undefined' && module.exports) module.exports={AutosaveQueue};
  if (typeof document==='undefined') return;

  const forms=[...document.querySelectorAll('form[data-draft-autosave]')];
  if (!forms.length || typeof fetch!=='function' || typeof AbortController==='undefined') return;
  const queue=new AutosaveQueue({send:async body=>{
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
    try {
      const response=await fetch('/structure-draft/autosave',{method:'POST',body,
        headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},
        credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if (!response.ok) throw Object.assign(new Error('Save rejected'),{
        blocked:[400,401,403,404,409,413].includes(response.status),
        code:response.status===409?'conflict':[401,403,404].includes(response.status)?'permission':
          [400,413].includes(response.status)?'invalid':response.status===429?'limited':'network'});
      if (!response.headers.get('Content-Type')?.startsWith('application/json')) throw new Error('Missing ACK');
      const result=await response.json();
      const url=new URL(result.resume,location.origin);
      if (!/^[a-f0-9]{32}$/.test(result.id) || typeof result.created_at!=='string' || result.created_at.length>64 ||
          url.origin!==location.origin || url.pathname!=='/structure-draft' || url.searchParams.get('id')!==result.id) {
        throw new Error('Invalid ACK');
      }
      return result;
    } finally {clearTimeout(timer);}
  }});
  for (const form of forms) {
    const status=form.querySelector('[data-draft-status]'), retry=form.querySelector('[data-draft-retry]');
    const buttons=[...form.querySelectorAll('button:not([type="button"])')];
    const entries=()=>[...form.elements].filter(e=>e.name && e.type!=='hidden' && !['submit','button'].includes(e.type))
      .map(e=>[e.name,e.value]);
    const job=queue.add({read:()=>({body:new URLSearchParams(new FormData(form)).toString(),value:JSON.stringify(entries())}),
      saved:(snapshot,result)=>{
        form.elements.namedItem('expected_draft').value=result.id;
        let link=form.querySelector('[data-draft-resume]');
        if (!link) {link=document.createElement('a'); link.dataset.draftResume=''; link.textContent='저장된 임시 입력 복구'; form.append(link);}
        link.href=result.resume;
        form.dispatchEvent(new CustomEvent('school-draft-saved',{bubbles:true,detail:{entries:JSON.parse(snapshot.value)}}));
      },
      state:(state,detail)=>{
        form.dataset.saveState=state; retry.hidden=state!=='error' || detail.blocked;
        for (const button of buttons) button.disabled=state==='saving';
        const error=detail.code==='conflict'?'다른 탭의 입력 또는 검수 기준이 변경됐습니다. 자동저장을 중지했습니다. 새 화면에서 대조하세요.':
          detail.code==='permission'?'로그인·수정 권한을 확인하세요. 자동저장을 중지했습니다.':
          detail.code==='invalid'?'입력값·길이를 확인하세요. 새 화면에서 다시 확인하세요.':
          detail.code==='limited'?'저장 요청이 많습니다. 1분 후 다시 시도하세요.':'통신 또는 서버 응답을 확인하지 못했습니다. 다시 시도하세요.';
        status.textContent=state==='idle'?'자동 임시저장 준비됨 · 정식 검수와 별도':
          state==='waiting'?'자동 임시저장 대기 · 아직 저장되지 않은 입력이 있습니다.':
          state==='saving'?'자동 임시저장 중 · 완료 표시를 확인하세요.':
          state==='saved'?'임시저장 완료 · '+detail.created_at+' · 정식 검수와 별도':
          '임시저장 실패 · '+error+' 현재 입력은 유지됩니다. 저장 완료를 확인하기 전에는 화면을 닫지 마세요.';
      }});
    form.addEventListener('input',()=>queue.changed(job));
    form.addEventListener('change',()=>queue.changed(job));
    form.addEventListener('compositionstart',()=>queue.hold(job,true));
    form.addEventListener('compositionend',()=>queue.hold(job,false));
    retry.addEventListener('click',()=>queue.retry(job));
  }
  // Do not race native explicit saves. If the loss-warning cancels submission,
  // resume timers; otherwise pagehide keeps unsent input in this document only.
  document.addEventListener('submit',event=>{
    queue.pause(); setTimeout(()=>{if(event.defaultPrevented) queue.resume();},0);
  },true);
  window.addEventListener('pagehide',()=>queue.pause());
  window.addEventListener('pageshow',()=>queue.resume());
})();
