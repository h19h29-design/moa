/* Offline synthetic originals only. Read actual DOM; never train or approve. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const playwright = require(process.env.MOA_PLAYWRIGHT_MODULE ||
  '/Users/mac-mini/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const hash = raw => crypto.createHash('sha256').update(raw).digest('hex');
const {snapshot} = require('./moa-browser-verify.cjs');

async function observe(page, doc) {
  const view = await snapshot(page);
  const map = new Map(view.blocks.map(b=>[b.source_block,b]));
  const paragraphs = doc.paragraph_ids.map(id=>(map.get(id)?.visible_text||'').trim().replace(/\s+/g,' '));
  const b = map.get(doc.table_id);
  if (!b || !b.visible || b.clipped) throw Error('table_not_visible');
  let physical = b.table_rows.map(row=>row.map(t=>t.trim()));
  let caption = b.caption||'';
  if (physical[0]?.length===1) {caption=physical[0][0];physical=physical.slice(1);}
  let headers=physical[0],rows=physical.slice(1);
  if (headers.length===6 && JSON.stringify(headers.slice(0,3))===JSON.stringify(headers.slice(3))) {
    rows=rows.flatMap(r=>[r.slice(0,3),r.slice(3,6)]).filter(r=>r.some(Boolean));
    headers=headers.slice(0,3);
  }
  // A card renderer may expose a caption externally instead of as a merged row.
  return {paragraphs,table:{headers,rows,caption,geometry:b.table_geometry||[]},
    page_overflow:view.page_overflow};
}

async function mutate(page, doc, v) {
  await page.evaluate(({doc,v})=>{
    if (v.action==='replace'||v.action==='remove') {
      const p=document.querySelector('[data-moa-block="'+doc.paragraph_ids[v.index]+'"]');
      const node=p.querySelector('p,h2,h3')||p;
      node.textContent=v.action==='remove'?'':node.textContent.replace(/\d+/,m=>String(Number(m)+1));
    } else if (['reorder','swap','drop_row'].includes(v.action)) {
      const wrapper=document.querySelector('[data-moa-block="'+doc.table_id+'"]');
      const primary=wrapper.querySelector('.moa-cards,.moa-timeline,.moa-scroll');
      const table=primary?.querySelector('table');
      if (table) {
        const rows=[...table.rows];
        // 6-column repetition is unrolled in the DOM, not repaired from native facts.
        const hi=rows.findIndex(r=>r.cells.length>=2);
        if (rows[hi].cells.length===6) {
          const body=table.tBodies[0];
          const next=[];
          for(const row of rows.slice(hi)) {
            for(const offset of [0,3]) {
              if(row===rows[hi] && offset===3)continue;
              const tr=document.createElement('tr');
              [...row.cells].slice(offset,offset+3).forEach(c=>tr.append(c.cloneNode(true)));
              next.push(tr);
            }
          }
          body.replaceChildren(...next);
        }
        const data=[...table.rows].filter(r=>r.cells.length>=2).slice(1);
        if(data.length!==2)throw Error('unexpected_table_rows');
        if(v.action==='reorder') data[0].parentElement.insertBefore(data[1],data[0]);
        if(v.action==='drop_row') data[1].remove();
        if(v.action==='swap') {
          const a=data[0].lastElementChild,b=data[1].lastElementChild,t=a.textContent;
          a.textContent=b.textContent;b.textContent=t;
        }
      } else {
        const cards=[...primary.children];
        // Header KV row belongs to the evidence; only reorder the two data rows.
        const data=cards.slice(1);
        if(data.length!==2)throw Error('unexpected_cards');
        if(v.action==='reorder')primary.insertBefore(data[1],data[0]);
        if(v.action==='drop_row')data[1].remove();
        if(v.action==='swap') {
          const a=data[0].querySelector('.moa-v'),b=data[1].querySelector('.moa-v'),t=a.textContent;
          a.textContent=b.textContent;b.textContent=t;
        }
      }
    }
  },{doc,v});
}

async function main() {
  const root=path.resolve(process.argv[2]||'');
  if(!root.startsWith('/Volumes/KIOXIA-2TB/'))throw Error('external_private_root_required');
  const out=path.join(root,'DOM_OBSERVATIONS.json');
  if(fs.existsSync(out))throw Error('observations_already_exist');
  const raw=fs.readFileSync(path.join(root,'DOM_SPEC.json')),spec=JSON.parse(raw);
  const browser=await playwright.chromium.launch({headless:true,
    executablePath:process.env.MOA_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context=await browser.newContext({viewport:{width:393,height:852}});
  // Offline; originals and synthetic HTML never leave this machine.
  await context.route('**/*',route=>route.abort());
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.name));
  const documents=[];
  try {
    for(const doc of spec.documents) {
      const html=fs.readFileSync(path.join(root,doc.html_file));
      if(hash(html)!==doc.html_sha256)throw Error('candidate_html_hash');
      await page.setContent(html.toString(),{waitUntil:'domcontentloaded'});
      const baseline=await observe(page,doc);
      const variants=[];
      for(const v of spec.variants) {
        await page.setContent(html.toString(),{waitUntil:'domcontentloaded'});
        await mutate(page,doc,v);
        variants.push({name:v.name,observation:await observe(page,doc)});
      }
      documents.push({source_sha256:doc.source.sha256,html_sha256:doc.html_sha256,
        baseline,variants,console_errors:[...errors]});
    }
    fs.writeFileSync(out,JSON.stringify({engine:'Playwright_Chrome_actual_DOM',spec_sha256:hash(raw),
      offline:true,training_started:false,human_decisions_submitted:0,documents}),{mode:0o600,flag:'wx'});
    console.log(JSON.stringify({documents:documents.length,variants:documents.reduce((n,d)=>n+d.variants.length,0),
      console_errors:errors.length,external_requests_allowed:false}));
  } finally {await context.close();await browser.close();}
}

if(require.main===module)main().catch(e=>{console.error(JSON.stringify({error:e.name,reason:e.message.split('\n')[0]}));process.exit(1);});
module.exports={observe,mutate};
