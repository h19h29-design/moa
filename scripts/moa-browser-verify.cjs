/* Authenticated actual-DOM evidence, private output only. No human decisions. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const playwright = require(process.env.MOA_PLAYWRIGHT_MODULE ||
  '/Users/mac-mini/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args = Object.fromEntries(process.argv.slice(2).reduce((out,v,i,a)=>
  v.startsWith('--') ? [...out,[v.slice(2),a[i+1]]] : out, []));

function password() {
  if (process.env.MOA_REVIEW_PASSWORD_FILE) return fs.readFileSync(process.env.MOA_REVIEW_PASSWORD_FILE,'utf8').trim();
  // File -> encrypted SSH pipe -> private memory -> login form. Never argv or logs.
  return execFileSync('ssh',['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','ds925-home','python3 -'],{
    input:"from pathlib import Path\nfor line in Path('/volume2/moa/app/.env').read_text().splitlines():\n if line.startswith('MOA_REVIEW_TOKEN='): print(line.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))\n",
    encoding:'utf8',timeout:15000}).trim();
}

async function snapshot(page) {
  return page.evaluate(()=>{
    const visible = e => {
      for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden'||s.opacity==='0')return false;}
      const r=e.getBoundingClientRect();return r.width>0 && r.height>0;
    };
    const clipped = e => [e,...e.querySelectorAll('*')].some(n=>{
      if(n.closest('details:not([open])') || !visible(n) || n.closest('.moa-scroll'))return false;
      const s=getComputedStyle(n);return /hidden|clip/.test(s.overflowX+s.overflowY) &&
        (n.scrollHeight>n.clientHeight+2||n.scrollWidth>n.clientWidth+2);
    });
    return {
      title:document.title, page_overflow:document.documentElement.scrollWidth>innerWidth+2,
      unbound_content:[...document.querySelectorAll('.moa-notice p,.moa-notice table')].some(e=>
        !e.closest('[data-moa-block]') && visible(e) && e.innerText.trim()),
      blocks:[...document.querySelectorAll('[data-moa-block]')].map(e=>{
        const b={source_block:e.dataset.moaBlock,type:e.dataset.moaType,visible_text:e.innerText,
          visible:visible(e),clipped:clipped(e)};
        if(b.type==='table'){
          const primary=e.querySelector('.moa-cards,.moa-timeline,.moa-scroll');
          const table=primary?.querySelector('table');
          if(table){
            b.table_rows=[...table.rows].map(r=>[...r.cells].map(c=>c.innerText));
            const occupied=new Set();const geometry=[];
            [...table.rows].forEach((row,ri)=>{let col=0;
              [...row.cells].forEach(cell=>{while(occupied.has(ri+','+col))col++;
                geometry.push([ri,col,cell.rowSpan,cell.colSpan]);
                for(let r=ri;r<ri+cell.rowSpan;r++)for(let c=col;c<col+cell.colSpan;c++)occupied.add(r+','+c);
                col+=cell.colSpan;});});
            if(geometry.some(c=>c[2]>1||c[3]>1))b.table_geometry=geometry;
          }
          else if(primary?.querySelector('.moa-card')){
            const cards=[...primary.querySelectorAll(':scope > .moa-card')];const first=cards[0];
            b.table_rows=[[first.querySelector('.moa-first-label')?.innerText||'',...[...first.querySelectorAll('.moa-k')].map(x=>x.innerText)],
              ...cards.map(c=>[c.querySelector('.moa-row-title')?.innerText||'',...[...c.querySelectorAll('.moa-v')].map(x=>x.innerText)])];
          }else if(primary?.classList.contains('moa-timeline')){
            b.table_rows=[...primary.children].map(c=>[...c.children].map(x=>x.innerText));
          }else if(primary){b.table_rows=[...primary.children].map(c=>[...c.children].map(x=>x.innerText));}
          else b.table_rows=[];
          b.caption=e.querySelector('.moa-block-caption')?.innerText||'';
          b.note=e.querySelector('.moa-block-note')?.innerText||'';
        }
        return b;
      }),
      attachments:[...document.querySelectorAll('.moa-attachments a')].map(a=>({text:a.innerText,href:a.getAttribute('href')})),
    };
  });
}

async function main(){
  if(!args.base||!args.input||!args.output)throw Error('requires --base --input --output');
  const spec=JSON.parse(fs.readFileSync(args.input,'utf8'));
  fs.mkdirSync(args.output,{recursive:true,mode:0o700});
  const browser=await playwright.chromium.launch({headless:true,
    executablePath:process.env.MOA_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context=await browser.newContext({viewport:{width:1280,height:900},acceptDownloads:true});
  const page=await context.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.name));
  try{
    const response=await page.goto(args.base,{waitUntil:'domcontentloaded'});
    const initial={status:response.status(),title:await page.title(),login:await page.locator('input[type=password]').count()};
    if(initial.login){const secret=password();if(!secret)throw Error('review credential unavailable');
      await page.locator('input[type=password]').fill(secret);
      await Promise.all([page.waitForURL(args.base.replace(/\/$/,'')+'/'),page.locator('button[type=submit],button.ok').first().click()]);}
    if(await page.locator('input[type=password]').count())throw Error('login failed');
    const results=[];
    for(const item of spec.documents){
      const query=new URLSearchParams({id:item.mobile_id,revision:item.revision_id});
      const notice=await page.goto(args.base+'/notice?'+query,{waitUntil:'domcontentloaded'});
      if(notice.status()!==200)throw Error('authenticated notice failed');
      const result={mobile_id:item.mobile_id,revision_id:item.revision_id,initial,notice_status:notice.status(),viewports:[],downloads:[]};
      // Read-only downloads via the authenticated browser request context.
      for(const file of item.files){
        const url=args.base+'/mobile/file?'+new URLSearchParams({id:item.mobile_id,file:file.id});
        const response=await context.request.get(url);const content=await response.body();
        const sha=crypto.createHash('sha256').update(content).digest('hex');
        result.downloads.push({file_id:file.id,status:response.status(),sha256:sha,hash_matches:sha===file.sha256});
      }
      const html=await context.request.get(args.base+'/mobile/html?id='+item.mobile_id);
      const htmlBody=await html.body();
      result.html_download_verified=html.status()===200 && htmlBody.includes(Buffer.from('data-moa-block')) &&
        item.files.every(f=>htmlBody.includes(Buffer.from(f.filename)));
      const bundle=await context.request.get(args.base+'/mobile/bundle?id='+item.mobile_id);
      if(bundle.status()===200){
        const entries=JSON.parse(execFileSync('python3',['-c',
          "import sys,io,zipfile,hashlib,json;z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()));print(json.dumps({n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}))"],
          {input:await bundle.body(),encoding:'utf8',maxBuffer:1024*1024}));
        result.bundle_download_verified=Boolean(entries['index.html']) && item.files.every(f=>
          entries['attachments/'+f.sha256.slice(0,12)+'-'+f.filename.replace(/[\\/\x00-\x1f]/g,'_')]===f.sha256);
      }else result.bundle_download_verified=false;
      for(const width of [360,393,430]){
        await page.setViewportSize({width,height:852});
        const response=await page.goto(args.base+'/mobile/preview?'+query,{waitUntil:'networkidle'});
        const observation=await snapshot(page);
        result.viewports.push({width,http_status:response.status(),
          html_sha256:crypto.createHash('sha256').update(await response.body()).digest('hex'),...observation});
        if(width===393)await page.screenshot({path:path.join(args.output,item.mobile_id+'-393.png'),fullPage:true});
      }
      result.console_errors=errors.splice(0);result.browser_plugin='absent; regular Playwright with installed Chrome';
      fs.writeFileSync(path.join(args.output,item.mobile_id+'.json'),JSON.stringify(result,null,2),{mode:0o600});
      results.push({mobile_id:item.mobile_id,viewports:result.viewports.length,
        download_hashes_match:result.downloads.every(d=>d.status===200&&d.hash_matches),console_errors:result.console_errors.length});
    }
    console.log(JSON.stringify({login_verified:true,documents:results}));
  }finally{await context.close();await browser.close();}
}
main().catch(e=>{console.error(JSON.stringify({error:e.name,message:e.message.split('\n')[0]}));process.exit(1)});
