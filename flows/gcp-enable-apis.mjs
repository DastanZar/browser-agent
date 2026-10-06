// Example flow: enable a list of APIs in a Google Cloud project through the console UI.
//   node replay.mjs flows/gcp-enable-apis.mjs project=my-proj apis=youtube.googleapis.com,drive.googleapis.com
// (The one-line CLI equivalent is `gcloud services enable ... --project my-proj`; this flow exists
// as a template for console pages that have no CLI.)
export default async (page, { project, apis }, log) => {
  if (!project || !apis) throw new Error('need project=<id> apis=<a,b,...>');
  for (const api of apis.split(',').map((s) => s.trim()).filter(Boolean)) {
    await page.goto(`https://console.cloud.google.com/apis/library/${api}?project=${project}`);
    const enable = page.getByRole('button', { name: /^enable$/i });
    const manage = page.getByRole('button', { name: /^manage$/i });
    await enable.or(manage).first().waitFor();
    if (await manage.isVisible()) { log(`${api}: already enabled`); continue; }
    await enable.click();
    await page.waitForURL(/\/apis\/api\/.*\/(overview|metrics)/, { timeout: 120000 });
    log(`${api}: enabled`);
  }
};
