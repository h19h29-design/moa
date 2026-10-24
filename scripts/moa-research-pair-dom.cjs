// Read the real rendered DOM. Authored facts/oracle/gold never enter the browser.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const {createRequire} = require('node:module');
const fromRuntime = createRequire(process.env.MOA_NODE_MODULES + '/package.json');
const {chromium} = fromRuntime('playwright');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = process.argv[3];
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext({viewport: {width: 393, height: 852}});
  await context.route('**/*', route => route.abort());
  const page = await context.newPage(), observed = {};
  let pageErrors = 0;
  page.on('pageerror', () => {pageErrors++;});
  try {
    for (const item of input) {
      const markup = fs.readFileSync(item.html_path, 'utf8');
      await page.setContent(markup);
      const blocks = await page.locator('[data-source]').evaluateAll(elements =>
        elements.map((el, i) => ({id: 'dom-' + i,
          source_refs: el.dataset.source.split(/\s+/).filter(Boolean), visible_text: el.innerText})));
      observed[item.case_id] = {html_sha256: sha(markup), blocks,
        observer: 'chromium-innerText-v1', network_blocked: true, page_errors: pageErrors};
    }
  } finally {await context.close(); await browser.close();}
  fs.writeFileSync(output, JSON.stringify(observed), {flag: 'wx', mode: 0o600});
  console.log(JSON.stringify({observed: Object.keys(observed).length, page_errors: pageErrors}));
})().catch(error => {console.error(JSON.stringify({status: 'DOM_FAILED', type: error.name})); process.exit(1);});
