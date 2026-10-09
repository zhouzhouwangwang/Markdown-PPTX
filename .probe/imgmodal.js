// 探针路径配置：优先读 PROBE_PLAYWRIGHT 环境变量（tools/run-tests.ps1 注入），
// 否则回退到仓库内 .test-runtime 的相对路径。
const path = require('path');
const _pwPath = process.env.PROBE_PLAYWRIGHT
  || path.resolve(__dirname, '..', '.test-runtime', 'node_modules', 'playwright');
const { chromium } = require(_pwPath);
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('http://127.0.0.1:19000/', { waitUntil: 'networkidle' });

  const r = {};
  // 1. modal exists, hidden by default; button opens it
  r.modalExists = await page.$eval('#imgModal', (el) => !!el && el.hidden);
  await page.click('#btnImg');
  r.buttonOpensModal = await page.$eval('#imgModal', (el) => !el.hidden);

  // 2. four type cards; per-type config isolation
  r.fourTypes = await page.$$eval('#imgModal .typecard', (els) => els.length === 4);
  await page.click('#imgModal .typecard[data-type="logo"]');
  r.logoLean = await page.evaluate(() => {
    // 计算样式（真实渲染），不是 hidden 属性——防止 CSS display 覆盖 hidden 造成假绿
    const show = (s) => { const el = document.querySelector(s); return !!el && getComputedStyle(el).display !== "none"; };
    return !show('#cfgPin') && !show('#cfgFigure') && !show('#cfgBg') && show('#cfgLogo');
  });
  await page.click('#imgModal .typecard[data-type="pin"]');
  r.pinConfig = await page.evaluate(() => {
    const show = (s) => { const el = document.querySelector(s); return !!el && getComputedStyle(el).display !== "none"; };
    return show('#cfgPin') && !show('#cfgFigure') && !show('#cfgBg') && !show('#cfgLogo');
  });
  await page.click('#imgModal .typecard[data-type="background"]');
  r.bgConfig = await page.evaluate(() => {
    const el = document.querySelector('#cfgBg');
    return el && !el.hidden && el.offsetParent !== null;
  });

  // 3. full flow: pin + br + upload -> syntax inserted
  await page.click('#imgModal .typecard[data-type="pin"]');
  await page.click('#pinGrid button[data-pin="br"]');
  await page.fill('#pinW', '25');
  await page.setInputFiles('#imgFile', 'assets/blue.jpg');
  await page.waitForTimeout(1500);
  const mdv = await page.$eval('#md', (el) => el.value);
  const line = mdv.split('\n').find((l) => l.includes('pin='));
  r.uploadSyntax = /!\[贴图\]\(assets\/[^\)]+\)\{pin=br, w=25%\}/.test(line || '');
  r.modalClosesAfterInsert = await page.$eval('#imgModal', (el) => el.hidden);

  // 4. right-click menu on editor
  await page.click('#btnClear');
  await page.evaluate(() => localStorage.removeItem('rcTipShown'));
  await page.reload({ waitUntil: 'networkidle' });
  r.rcTip = await page.$eval('#rcTip', (el) => !!el);
  await page.click('#md');
  await page.keyboard.type('# 标题\n\n## 节\n\n- 要点\n');
  const box = await page.$eval('#md', (el) => ({ w: el.clientWidth, h: el.clientHeight }));
  await page.mouse.click(box.w * 0.5, box.h * 0.5, { button: 'right' });
  r.ctxMenu = await page.$eval('#ctxMenu', (el) => !!el && !el.hidden);
  const items = await page.$$eval('#ctxMenu [data-act]', (els) => els.map((e) => e.dataset.act));
  r.menuItems = items.includes('pin') && items.includes('copy') && items.includes('paste') && items.includes('selectAll') && items.includes('delete');
  await page.click('#ctxMenu [data-act="pin"]');
  r.menuOpensModalAtPin = await page.evaluate(() => {
    const m = document.querySelector('#imgModal');
    const sel = document.querySelector('#imgModal .typecard.on');
    return m && !m.hidden && sel && sel.dataset.type === 'pin';
  });
  await page.keyboard.press('Escape');

  // 5. paste fallback does not throw
  await page.mouse.click(box.w * 0.5, box.h * 0.5, { button: 'right' });
  await page.click('#ctxMenu [data-act="selectAll"]');
  await page.mouse.click(box.w * 0.5, box.h * 0.5, { button: 'right' });
  await page.click('#ctxMenu [data-act="delete"]');
  r.deleteWorks = (await page.$eval('#md', (el) => el.value)) === '';
  r.jsErrors = errors;

  console.log(JSON.stringify(r, null, 1));
  const ok = r.modalExists && r.buttonOpensModal && r.fourTypes && r.logoLean && r.pinConfig &&
    r.bgConfig && r.uploadSyntax && r.modalClosesAfterInsert && r.ctxMenu && r.menuItems &&
    r.menuOpensModalAtPin && r.rcTip && r.deleteWorks && r.jsErrors.length === 0;
  console.log('ALL:', ok ? 'PASS' : 'FAIL');
  await browser.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
