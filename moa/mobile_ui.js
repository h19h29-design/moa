/* Fixed controls edit structured text, never execute uploaded markup. */
(() => {
  const processing=document.getElementById('conversion-state');
  if(processing){
    const poll=setInterval(async()=>{
      try{const res=await fetch('/mobile/state?id='+encodeURIComponent(processing.dataset.id));
        if(res.status===401){clearInterval(poll);location.assign('/login');return;}
        if(res.ok && (await res.json()).state!=='processing'){clearInterval(poll);location.reload();}
      }catch{processing.textContent='연결을 확인하고 페이지를 새로고침하세요.';}
    },2500);
  }
  const json = document.getElementById('blocks');
  const target = document.getElementById('block-editor');
  if (!json || !target) return;
  let blocks;
  const labels = {key_value_cards:'정보 카드', grade_cards:'항목별 카드', timeline:'일정 목록', scroll_table:'표 유지', comparison:'비교 표', read_only_form:'읽기 전용 표'};
  const node = (name,text) => { const el=document.createElement(name); if(text)el.textContent=text; return el; };
  const sync = () => { json.value=JSON.stringify(blocks,null,1); };
  const field = (parent,title,value,change,type='text') => {
    const label=node('label',title);
    const el=node(type==='textarea'?'textarea':'input');
    if(type!=='textarea')el.type=type;
    if(type==='checkbox')el.checked=!!value; else el.value=value ?? '';
    el.addEventListener('input',()=>{ change(type==='checkbox'?el.checked:type==='number'?Number(el.value):el.value); sync(); });
    label.append(el); parent.append(label); return el;
  };
  function render(){
    target.replaceChildren();
    blocks.forEach((b,index)=>{
      const section=node('section');section.className='editor-block';
      section.append(node('h3',`구역 ${b.source_block ?? index} · ${b.type==='table'?'표':'본문'}`));
      if(b.type==='paragraph')field(section,'안내 내용',b.text,v=>b.text=v,'textarea');
      if(b.type==='table'){
        const label=node('label','모바일 표현');const select=node('select');
        for(const [key,text] of Object.entries(labels)){const opt=node('option',text);opt.value=key;opt.selected=key===b.layout;select.append(opt);}
        select.addEventListener('change',()=>{b.layout=select.value;sync();});label.append(select);section.append(label);
        const dimensions=node('div');dimensions.className='coords';
        field(dimensions,'행 수',b.table.rows,v=>b.table.rows=v,'number');
        field(dimensions,'열 수',b.table.cols,v=>b.table.cols=v,'number');section.append(dimensions);
        b.table.cells.forEach(c=>{
          const row=node('div');row.className='cell-editor';
          field(row,`행 ${c.row} / 열 ${c.col}`,c.text,v=>c.text=v,'textarea');
          const coords=node('div');coords.className='coords';
          field(coords,'행',c.row,v=>c.row=v,'number');field(coords,'열',c.col,v=>c.col=v,'number');
          field(coords,'세로 병합',c.rowspan,v=>c.rowspan=v,'number');
          field(coords,'가로 병합',c.colspan,v=>c.colspan=v,'number');field(coords,'헤더',c.header,v=>c.header=v,'checkbox');
          row.append(coords);section.append(row);
        });
        field(section,'단위·각주 (원문 정보를 그대로 입력)',b.note,v=>b.note=v,'textarea');
      }
      target.append(section);
    });
  }
  try{blocks=JSON.parse(json.value);render();}catch{target.textContent='구조 JSON을 확인하세요.';}
  json.addEventListener('change',()=>{try{blocks=JSON.parse(json.value);render();json.setCustomValidity('');}catch{json.setCustomValidity('JSON 형식을 확인하세요.');}});
})();
