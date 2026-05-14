// WebSocket helpers. The Next.js dev server proxies HTTP `/api/*` to the
// FastAPI service, but WS isn't proxied through `rewrites()`, so we
// connect directly. NEXT_PUBLIC_WS_BASE controls the destination
// (defaults to localhost:8000 for dev; reverse-proxy URL in prod).

export function wsBase(): string {
  if (typeof window === "undefined") return "ws://localhost:8001";
  const fromEnv = process.env.NEXT_PUBLIC_WS_BASE;
  if (fromEnv) return fromEnv;
  // Last-resort fallback: assume the API is on the same host as the web
  // origin at port 8001 (the prod default after we moved off the conflict
  // with grafana on 8000). For a custom port, set NEXT_PUBLIC_WS_BASE.
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.hostname}:8001`;
}

export function runStreamUrl(runId: string): string {
  return `${wsBase()}/runs/${runId}/stream`;
}

export function chatStreamUrl(runId: string): string {
  return `${wsBase()}/runs/${runId}/chat/stream`;
}

export function priceStreamUrl(ticker: string): string {
  return `${wsBase()}/streaming/price/${ticker}`;
}

export function newsStreamUrl(ticker: string): string {
  return `${wsBase()}/streaming/news/${ticker}`;
}

export function combinedStreamUrl(ticker: string): string {
  return `${wsBase()}/streaming/${ticker}`;
}
