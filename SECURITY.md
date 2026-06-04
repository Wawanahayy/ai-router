# Security Policy

AI Router handles local API keys, upstream provider keys, provider URLs, request
logs, prompts, and usage data. Treat those values as sensitive.

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
- Cloudflare tunnel tokens or service credentials

## Recommended deployment practices

- Keep dashboard authentication enabled for exposed deployments.
- Use strong dashboard passwords.
- Rotate local and upstream keys after accidental exposure.
- Put AI Router behind trusted network controls, a reverse proxy, or Cloudflare
  Tunnel when exposing it outside localhost.
- Review logs before sharing them.

## Dependency security

The GitHub CI checks Python and web dependencies. Dependabot is configured for
weekly pip and npm update checks.
