/**
 * tg-vault CORS Proxy — Cloudflare Worker
 *
 * Solves the CORS issue with Telegram's file download endpoint.
 * The bot token stays in the Worker's encrypted environment variables.
 *
 * Setup (2 minutes, free):
 * 1. Go to https://dash.cloudflare.com → Workers & Pages → Create Worker
 * 2. Replace code with this file
 * 3. Save and deploy
 * 4. Settings → Variables → Add: TG_BOT_TOKEN = your-token (encrypt it)
 * 5. In the web app Settings, set CORS Proxy URL to:
 *    https://tg-proxy.your-name.workers.dev/?path=
 */

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, OPTIONS',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Max-Age': '86400',
        },
      });
    }

    const url = new URL(request.url);
    const filePath = url.searchParams.get('path');

    if (!filePath) {
      return new Response(
        JSON.stringify({ error: 'Missing ?path= parameter' }),
        { status: 400, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' } }
      );
    }

    const token = env.TG_BOT_TOKEN;
    if (!token) {
      return new Response(
        JSON.stringify({ error: 'TG_BOT_TOKEN not set' }),
        { status: 500, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' } }
      );
    }

    const tgUrl = `https://api.telegram.org/file/bot${token}/${filePath}`;
    let tgRes;
    try {
      tgRes = await fetch(tgUrl);
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), {
        status: 502,
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
      });
    }

    const headers = new Headers(tgRes.headers);
    headers.set('Access-Control-Allow-Origin', '*');
    headers.set('Access-Control-Expose-Headers', 'Content-Length, Content-Type');

    return new Response(tgRes.body, { status: tgRes.status, headers });
  },
};
