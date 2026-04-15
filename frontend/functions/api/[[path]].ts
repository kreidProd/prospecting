// Cloudflare Pages Function that proxies /api/* requests to the Railway backend.
// Configure the backend URL by setting API_BASE_URL in Pages → Settings → Environment variables.
// Example: API_BASE_URL = https://prospector-backend.up.railway.app

interface Env {
  API_BASE_URL: string
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const backend = env.API_BASE_URL
  if (!backend) {
    return new Response(
      JSON.stringify({ detail: 'API_BASE_URL not configured in Pages environment' }),
      { status: 500, headers: { 'content-type': 'application/json' } },
    )
  }

  const url = new URL(request.url)
  const target = backend.replace(/\/$/, '') + url.pathname + url.search

  // Forward the request. Keep body/headers/method; disable redirect following so
  // browser auth prompts (401 → WWW-Authenticate) surface correctly.
  const init: RequestInit = {
    method: request.method,
    headers: request.headers,
    body:
      request.method === 'GET' || request.method === 'HEAD'
        ? undefined
        : request.body,
    redirect: 'manual',
  }

  const resp = await fetch(target, init)

  // Strip any backend CORS headers — same-origin from the browser's POV, no need.
  const outHeaders = new Headers(resp.headers)
  outHeaders.delete('access-control-allow-origin')
  outHeaders.delete('access-control-allow-credentials')

  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText,
    headers: outHeaders,
  })
}
