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
  page.on('pageerror', (e) => errors.push('pageerror: ' + String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto('http://127.0.0.1:19000/', { waitUntil: 'networkidle' });
  const before = await page.$eval('#industry', (el) => el.classList.contains('off'));
  await page.selectOption('#style', 'cards');
  const after = await page.$eval('#industry', (el) => el.classList.contains('off'));
  const enabledAlways = await page.$eval('#industry', (el) => !el.disabled);
  const theme = await page.evaluate(() => currentTheme());
  const industryValue = await page.$eval('#industry', (el) => el.value);
  await page.selectOption('#industry', 'med');
  const themeAfter = await page.evaluate(() => currentTheme());
  console.log(JSON.stringify({
    dimmedBeforeStyleChange: before,
    dimmedAfterSelectingCards: after,
    alwaysInteractable: enabledAlways,
    themeWithCards: theme,
    industryDefault: industryValue,
    themeAfterPickMed: themeAfter,
    jsErrors: errors,
  }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
