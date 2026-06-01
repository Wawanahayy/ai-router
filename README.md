# AI Router

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

GitHub: https://github.com/Wawanahayy/ai-router

AI Router is a small self-hosted gateway for routing AI chat requests across
multiple upstream providers, models, and API keys.

It exposes OpenAI-compatible and Anthropic-compatible endpoints, so existing
clients can point to AI Router instead of calling every upstream provider
directly. You can add custom AI providers, rotate multiple upstream keys,
configure fallback routing, inspect request logs, and manage everything from a
web dashboard.

## Features

- OpenAI-compatible endpoint: `/v1/chat/completions`
- Anthropic Messages-compatible endpoint: `/v1/messages`
- Custom OpenAI-compatible and Anthropic-compatible upstream providers
- Multiple upstream API keys per provider
- Provider/model aliases and combo routing
- Streaming proxy with heartbeat handling for slow upstream responses
- Request logs, token usage tracking, and dashboard metrics
- Optional dashboard login and local API keys

## How It Works

AI Router sits between your app and upstream AI providers.

```text
Your app / bot / client
        |
        | OpenAI or Anthropic compatible request
        v
AI Router
        |
        | Select provider, model, key, fallback, and stream handling
        v
Upstream AI provider
```

Instead of hardcoding many provider URLs and keys in your application, you add
them once to AI Router. Your application only needs the AI Router endpoint and a
local `ar-...` API key when proxy API key protection is enabled.

## Requirements

- Python 3.11+
- Node.js 20+ for building the dashboard

## Quick Start

Clone the repository:

```bash
git clone https://github.com/Wawanahayy/ai-router.git
cd ai-router
```

Using the helper script:

```bash
bash start.sh setup
bash start.sh run
```

Manual setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

The server defaults to:

```text
http://localhost:32128
```

Open the dashboard in your browser:

```text
http://localhost:32128
```

The default dashboard password is:

```text
ABC12345
```

Change the password before exposing the dashboard to the internet.

## Dashboard Build

Using the helper script:

```bash
bash start.sh build-web
```

Manual build:

```bash
cd web
npm install
npm run build
```

The backend serves the built dashboard from `ai_router/static`.

For local dashboard development, use Vite:

```bash
bash start.sh dev-web
```

The Vite dev server proxies `/api` and `/v1` requests to the backend on port
`32128`.

## Client Endpoints

OpenAI-compatible:

```text
POST http://localhost:32128/v1/chat/completions
Authorization: Bearer ar-your-local-key
```

Anthropic-compatible:

```text
POST http://localhost:32128/v1/messages
Authorization: Bearer ar-your-local-key
anthropic-version: 2023-06-01
```

## Example Requests

OpenAI-compatible chat completions:

```bash
curl http://localhost:32128/v1/chat/completions \
  -H "Authorization: Bearer ar-your-local-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-alias",
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "stream": true
  }'
```

Anthropic-compatible messages:

```bash
curl http://localhost:32128/v1/messages \
  -H "Authorization: Bearer ar-your-local-key" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "your-model-alias",
    "max_tokens": 512,
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "stream": true
  }'
```

## Providers, Keys, and Models

AI Router separates routing into a few parts:

- Providers are upstream services, such as OpenAI-compatible APIs, Anthropic-compatible APIs, or custom providers.
- Keys are upstream provider API keys. One provider can have many keys.
- Empty key labels are automatically named `apikey-1`, `apikey-2`, and so on.
- Models are provider model IDs or local aliases that your client can request.
- Combos let one local model route through multiple provider/model choices.
- Fallback lets AI Router try another provider, key, or model when one attempt fails.

This is useful when you want a single endpoint for bots, tools, agents, or apps
while still being able to swap providers behind the scenes.

## Streaming Behavior

AI Router supports streaming responses and is designed to wait for slow upstream
models instead of cutting off long thinking responses too early. Slow model
startup, provider queueing, large prompts, and reasoning models can all increase
time before the first streamed token.

When debugging slow requests, check:

- The request logs page in the dashboard
- `server.out.log` and `server.err.log`
- Provider key health and cooldown state
- Upstream provider status, rate limits, and model availability
- Prompt size and token usage

## Configuration

Copy `.env.example` to `.env` and adjust:

```text
AI_ROUTER_PORT=32128
AI_ROUTER_HOST=0.0.0.0
AI_ROUTER_DB=./data/ai-router.db
AI_ROUTER_AUTH=false
AI_ROUTER_PASSWORD=ABC12345
```

Common settings:

- `AI_ROUTER_PORT`: backend port
- `AI_ROUTER_HOST`: bind host, usually `0.0.0.0` for servers
- `AI_ROUTER_DB`: SQLite database path
- `AI_ROUTER_AUTH`: enable or disable dashboard password login
- `AI_ROUTER_PASSWORD`: dashboard password

Do not commit `.env`, database files, API key files, logs, local virtualenvs, or generated reports.
Change the default password before exposing the dashboard publicly.

## Optional: Keep It Running With Cloudflare Tunnel

AI Router works locally without Cloudflare. Cloudflare Tunnel is optional and is
only needed if you want to open the dashboard/API from another device, another
network, or a temporary public URL without opening a server port.

Before installing Cloudflare Tunnel, decide whether you actually need remote
access. If you only use AI Router on the same machine, skip this section.

Install `cloudflared` first, then run AI Router and Cloudflare Tunnel as
services.

Install `cloudflared` from the official Cloudflare downloads page:

```text
https://developers.cloudflare.com/tunnel/downloads/
```

For a temporary `trycloudflare.com` URL:

```bash
cloudflared tunnel --url http://localhost:32128
```

That URL is temporary. If `cloudflared` stops or the server restarts, the URL
can change when the tunnel starts again.

To keep AI Router running on Linux with systemd, create:

```bash
sudo nano /etc/systemd/system/ai-router.service
```

Example service file:

```ini
[Unit]
Description=AI Router
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/ai-router
ExecStart=/root/ai-router/.venv/bin/python /root/ai-router/run.py
Restart=always
RestartSec=5
EnvironmentFile=/root/ai-router/.env

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-router
sudo systemctl status ai-router
```

To keep the temporary Cloudflare Tunnel running too, create:

```bash
sudo nano /etc/systemd/system/ai-router-cloudflare.service
```

Example service file:

```ini
[Unit]
Description=AI Router Cloudflare Temporary Tunnel
After=network.target ai-router.service
Requires=ai-router.service

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:32128
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-router-cloudflare
sudo systemctl status ai-router-cloudflare
```

View the generated Cloudflare URL:

```bash
journalctl -u ai-router-cloudflare -f
```

If you expose the dashboard through Cloudflare, set `AI_ROUTER_AUTH=true` and
change `AI_ROUTER_PASSWORD` before starting the service.

## Security Notes

- Treat upstream provider keys as secrets.
- Keep `.env` and database files out of Git.
- Enable proxy API key protection if the router is reachable by other users.
- Use a reverse proxy with HTTPS when exposing AI Router publicly.
- Rotate keys if a private database, `.env`, or log file was accidentally shared.
- Review request logs before publishing screenshots or issue reports.
- Keep unrelated local research/output folders out of the public repository.

## Project Structure

```text
ai_router/          Backend server, routing, streaming, database, and API code
ai_router/static/   Built dashboard served by the backend
web/                React dashboard source
data/               Local SQLite database directory, ignored by Git
import_keys.py      Helper script for importing provider keys
run.py              Backend entrypoint
```

## AI Agent Guide

AI coding agents and contributors can use [SKILLS.md](./SKILLS.md) for project
rules, validation commands, streaming notes, database cautions, and release
checks.

## Troubleshooting

If `npm install` fails because of a platform-specific package, remove
`node_modules` and reinstall on the target OS. Do not commit OS-specific native
dependencies as direct dependencies.

If requests do not appear in logs, confirm the client is calling AI Router and
not the upstream provider directly. Also check whether the request is using the
OpenAI endpoint or the Anthropic endpoint expected by the client.

If streaming feels stuck, the upstream model may still be preparing a response.
Large context, reasoning models, provider queueing, or temporary upstream
problems can delay the first token.

## License

This project is released under the MIT License.

You are free to use, copy, modify, distribute, and publish this software,
including for commercial projects. If you redistribute this project or a
modified version of it, keep the original copyright notice and license text.

This software is provided as-is, without warranty. You are responsible for how
you configure providers, API keys, access control, rate limits, and any costs
from upstream AI services.

See [LICENSE](./LICENSE) for the full license text.
