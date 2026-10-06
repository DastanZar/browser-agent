// Replay a saved flow in your logged-in Chrome (the one start-chrome.sh opened). No AI, no tokens.
//
//   node replay.mjs flows/gcp-enable-apis.mjs project=my-proj apis=gmail.googleapis.com,drive.googleapis.com
//
// A flow is a module whose default export is `async (page, vars, log) => {}`. Write one by hand,
// record one with `npx playwright codegen` and paste the body in, or ask Claude to turn a task it
// just did through the browser MCP into a flow so the next run is free.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [flowPath, ...args] = process.argv.slice(2);
if (!flowPath) {
  console.error('usage: node replay.mjs <flow.mjs> [key=value ...]  (env CDP=http://127.0.0.1:9222)');
  process.exit(2);
}
const vars = Object.fromEntries(args.map((a) => {
  const i = a.indexOf('=');
  return i < 0 ? [a, 'true'] : [a.slice(0, i), a.slice(i + 1)];
}));
const cdp = process.env.CDP || 'http://127.0.0.1:9222';

const flow = (await import(pathToFileURL(resolve(flowPath)).href)).default;
const browser = await chromium.connectOverCDP(cdp);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
page.setDefaultTimeout(Number(process.env.STEP_TIMEOUT_MS || 30000));
const log = (...m) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...m);

let code = 0;
try {
  await flow(page, vars, log);
  log('flow finished');
} catch (err) {
  code = 1;
  mkdirSync('failures', { recursive: true });
  const shot = `failures/${Date.now()}.png`;
  await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
  console.error(`flow failed: ${err.message}\nscreenshot: ${shot}  (tab left open so you can finish by hand)`);
}
if (code === 0 && !vars.keepTab) await page.close();
await browser.close(); // disconnects only; your Chrome keeps running
process.exit(code);
