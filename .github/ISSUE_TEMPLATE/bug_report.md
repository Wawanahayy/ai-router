---
name: Bug report
about: Report a problem with AI Router
title: "[Bug]: "
labels: bug
assignees: ""
---

## What happened?

Describe the visible problem. Include the exact error message if there is one.

## Expected behavior

What should AI Router have done instead?

## Steps to reproduce

1.
2.
3.

## Request details

- Endpoint: `/v1/chat/completions`, `/v1/messages`, dashboard, or other:
- Provider type: OpenAI-compatible, Anthropic-compatible, Claude CLI, or other:
- Model or combo name:
- Streaming enabled: yes/no:
- Fallback enabled: yes/no:
- Tool calling used: yes/no:

## Sanitized request body

Remove real API keys, prompts with private data, cookies, tokens, and provider secrets.

```json

```

## Environment

- OS:
- Python version:
- Node version:
- AI Router version or commit:
- Install method: `bash start.sh`, manual, systemd, Docker, or other:
- Runtime port:

## Logs

Paste only the relevant lines. Remove secrets before posting.

```text

```

## Checklist

- [ ] I removed all API keys, local `ar-...` keys, cookies, and provider tokens.
- [ ] I checked the dashboard logs page.
- [ ] I checked server logs or `journalctl` if running under systemd.
