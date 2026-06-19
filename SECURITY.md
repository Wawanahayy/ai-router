# Security Policy

AI Router handles local API keys, upstream provider keys, provider URLs, request
logs, prompts, and usage data. Treat those values as sensitive.

Public-compatible proxy endpoints include:

- `/v1/chat/completions`
- `/v1/responses`
- `/v1/messages`
- `/v1/models`

Management endpoints under `/api/*` can list and modify providers, upstream
keys, local `ar-...` keys, settings, logs, pricing, aliases, and combos. Do not
expose them without dashboard authentication.

## Reporting a vulnerability

Do not open a public issue with secrets, exploit details, tokens, database
contents, or private logs.

For now, report security issues by opening a GitHub issue with only a minimal
non-sensitive summary and ask for a private contact path. If GitHub private
vulnerability reporting is enabled for this repository, use that instead.

Include:

- A short description of the issue
- Affected endpoint or feature
- Impact
- Safe reproduction steps without real secrets
- Whether any key, token, cookie, or database content may have been exposed

## Sensitive data

Never post or commit:

- Upstream provider API keys
- Local AI Router `ar-...` API keys
- Dashboard passwords
- Cookies or bearer tokens
- `.env` files
- SQLite databases
- Request logs containing private prompts or secrets
- Expanded fallback chains containing provider, model, key labels, or upstream
  error details
- Cloudflare tunnel tokens or service credentials
- Local helper files that look like `apikey*.*` or contain replacement keys

## Recommended deployment practices

- Keep dashboard authentication enabled for exposed deployments.
- Use strong dashboard passwords.
- Rotate local and upstream keys after accidental exposure.
- Put AI Router behind trusted network controls, a reverse proxy, or Cloudflare
  Tunnel when exposing it outside localhost.
- Review logs before sharing them.
- Treat `/api/logs` and dashboard log details as sensitive because upstream
  errors can include prompt snippets, provider request IDs, quota details, or
  account-specific information.
- Keep `/api/keys`, `/api/local-keys`, and `/api/providers` behind dashboard
  authentication.
- If using `key_edit.py` or any similar local helper, pass secrets through a
  trusted terminal only and avoid shell history exposure when possible.

## Key rotation and replacement

AI Router can mark upstream keys as unavailable, cooldown, or model-locked after
provider errors. Operators can replace a bad key through the dashboard or the
localhost admin API.

When replacing keys:

- Confirm the key ID and provider before editing.
- Prefer editing the existing key record when you want to preserve labels and
  request history.
- After replacing an upstream key, the server clears cooldown/error fields and
  treats it as alive.
- Rotate upstream and local keys immediately after accidental exposure.
- Do not paste real keys into GitHub issues, screenshots, or public logs.

## Error and log handling

The router classifies upstream failures into categories such as context limit,
quota exhaustion, rate limit, overloaded, timeout, unsupported model, auth
failure, and generic upstream error.

These classifications are for routing and diagnostics only. They should not be
treated as a full security boundary. A malicious or broken upstream can still
return misleading error text.

When sharing logs:

- Redact API keys, bearer tokens, cookies, provider account IDs, and tunnel URLs.
- Redact private prompt or tool output content.
- Redact request IDs if they can be used with a provider support team to expose
  account information.
- Prefer short error summaries over full raw upstream responses.

## Responses compatibility

`/v1/responses` is a compatibility bridge that converts requests internally to
chat completions and converts the result back to a Responses-shaped payload.
Treat data sent to `/v1/responses` the same as chat data: prompts, tool results,
and function arguments can contain secrets.

## Dependency security

The GitHub CI checks Python and web dependencies. Dependabot is configured for
weekly pip and npm update checks.
