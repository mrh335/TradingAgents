# Running Claude in Cowork against this NAS

Cowork (Anthropic's cloud-hosted Claude environment) can't reach LAN IPs
like `192.168.2.34:8001` directly. To use the `tradingagents-briefs`
skill from a Cowork session, you need a public-internet endpoint that
forwards to the NAS API.

The recommended transport is a **Cloudflare Tunnel** — free, no port
forwarding, no static IP, TLS handled for you. There are two flavors:
a **quick tunnel** (zero setup, ephemeral URL) and a **named tunnel**
(5-min one-time setup, stable URL).

---

## Quick tunnel — try it now

No Cloudflare account, no DNS, no token. Run for the duration of one
Cowork session, then tear down. URL is a random `*.trycloudflare.com`.

On the NAS:

```bash
docker compose --profile tunnel up -d cloudflared
docker compose logs cloudflared 2>&1 | grep -E "trycloudflare.com"
```

You'll see a line like:

```
Your quick Tunnel has been created! Visit it at:
https://random-words-here.trycloudflare.com
```

Then in Cowork:

```bash
export TRADINGAGENTS_API=https://random-words-here.trycloudflare.com
export TRADINGAGENTS_WEB=https://random-words-here.trycloudflare.com   # if you tunnel the web too
```

The `tradingagents-briefs` skill picks up `TRADINGAGENTS_API` automatically.
Ask Claude: *"process pending briefs"* — same as you'd do on the LAN.

When you're done:

```bash
docker compose --profile tunnel down
```

Caveats:
- URL regenerates on every container restart (you'd update the env var
  in Cowork each time).
- No auth. Anyone who finds the URL can read pending requests and POST
  briefs. Fine for a 30-minute session, not for leaving running.
- The quick tunnel only proxies the **api** service (port 8000 inside
  the network). If you want the web UI tunneled too, see "Two tunnels"
  below.

---

## Named tunnel — the permanent setup

One-time setup, stable URL, optional Cloudflare Access for auth.

**Step 1 — create the tunnel in the Cloudflare dashboard**

1. Sign up at https://dash.cloudflare.com (free).
2. Add the domain you want to use (or use a Cloudflare-owned subdomain
   under `*.cfargotunnel.com` — works without owning a domain).
3. Go to **Zero Trust → Networks → Tunnels → Create a tunnel**.
4. Connector: **Cloudflared**. Name it `tradingagents-api`.
5. Copy the install token from the **Docker** tab — it's the long
   `eyJ...` string in the `--token` flag.

**Step 2 — wire the token into `.env`**

```bash
# In Z:/My Documents/code repo/active/hedge_trader/TradingAgents/.env
CLOUDFLARED_TOKEN=eyJ...your-token-here...
CLOUDFLARED_CMD=tunnel --no-autoupdate run
```

The `CLOUDFLARED_CMD` override switches the container from quick-tunnel
mode to named-tunnel mode (uses `TUNNEL_TOKEN`, ignores `--url`).

**Step 3 — map a hostname → service in the dashboard**

Back in the tunnel page, **Public Hostname** tab:

| Subdomain | Domain | Service |
|---|---|---|
| `tradingagents-api` | your-domain.com | `http://api:8000` |

Save. (If you want the web UI public too, add a second hostname →
`http://web:3000`.)

**Step 4 — start the tunnel**

```bash
docker compose --profile tunnel up -d cloudflared
docker compose logs cloudflared --tail 20
```

Look for `Registered tunnel connection` lines (you should see 4, one
per Cloudflare edge region).

**Step 5 — verify**

From any machine on the open internet:

```bash
curl -s https://tradingagents-api.your-domain.com/health
# {"status":"ok"}
```

If it works, you're done. From Cowork:

```bash
export TRADINGAGENTS_API=https://tradingagents-api.your-domain.com
```

…and `"process pending briefs"` works the same as locally.

---

## Optional — Cloudflare Access auth

Public tunnels are open to the internet by default. To require auth
(Google login, GitHub login, one-time codes via email, etc.):

1. **Zero Trust → Access → Applications → Add an application**.
2. Type: **Self-hosted**. Domain: the tunnel hostname.
3. Add a policy — e.g. *"allow only email markhoehne@gmail.com"*.

Once enforced, you'll need a service token for Cowork to authenticate.
Add it to the Cowork session as headers:

```bash
export TRADINGAGENTS_API_HEADERS="CF-Access-Client-Id: ...id..., CF-Access-Client-Secret: ...secret..."
```

(The skill doesn't currently use this env var — file a TODO when you
get there if you actually need Access auth.)

---

## Two tunnels — API + web UI

If you also want to reach `http://192.168.2.34:3001/history/...` links
from outside the LAN (e.g. open a brief from Cowork's reply), add a
second public hostname in the same tunnel:

| Subdomain | Domain | Service |
|---|---|---|
| `tradingagents` | your-domain.com | `http://web:3000` |

…and set:

```bash
export TRADINGAGENTS_WEB=https://tradingagents.your-domain.com
```

The skill's "view this run" links will use that base.

---

## Alternatives you'd consider instead

- **Tailscale Funnel** — similar idea, requires Tailscale install. Good
  if you already use Tailscale across your devices. Tunneled hostname is
  `*.ts.net`, free for personal use.
- **GitHub repo bridge** — NAS pushes a snapshot of `/sidecars/pending`
  to a private repo on a cron; Cowork pulls, processes, pushes briefs;
  NAS pulls and posts them. No public exposure, but async (cron
  latency) and needs more glue code on both sides.
- **ngrok / localtunnel** — works but ngrok rotates URLs on the free
  tier and has bandwidth caps. Cloudflare Tunnel beats it for free
  permanent use.

---

## Troubleshooting

**`docker compose logs cloudflared` shows `Unauthorized`**

The `TUNNEL_TOKEN` is wrong or stale. Generate a fresh token in the
Cloudflare dashboard and update `.env`.

**Tunnel connects but `curl /health` times out**

The hostname → service mapping in the dashboard is wrong. Verify:
- service is `http://api:8000` (internal container address, NOT the
  external `192.168.2.34:8001` port)
- the `cloudflared` container is on the same compose network as `api`
  (it is by default — both services are in the project's default
  network).

**Cowork session says "API is not reachable"**

Check `echo $TRADINGAGENTS_API` in the Cowork shell. If empty, the env
var isn't exported in that session. Set it again and retry. The skill
will fall back to `http://192.168.2.34:8001` if `TRADINGAGENTS_API` is
unset — which fails silently from Cowork.

**Need to rotate the tunnel**

```bash
docker compose --profile tunnel restart cloudflared
```

This pulls a new connection but reuses the same hostname → no Cowork
env update needed.
