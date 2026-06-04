---
name: Provider problem
about: Report provider, model, key, timeout, streaming, or fallback issues
title: "[Provider]: "
labels: provider
assignees: ""
---

## Provider details

- Provider name:
- Provider type: OpenAI-compatible / Anthropic-compatible / other:
- Base URL domain only, not the full secret URL:
- Auth type: bearer / x-api-key / custom header / query / none:
- Model ID:
- Prefix enabled: yes/no:
- Combo name if used:

## Problem type

- [ ] Provider test fails
- [ ] Model is routed to the wrong provider
- [ ] Model prefix does not resolve correctly
- [ ] Streaming hangs or stops early
- [ ] Timeout or first byte delay
- [ ] Fallback does not run as expected
- [ ] Token usage or logs look wrong
- [ ] Provider binary or environment problem
- [ ] Other:

## What happened?


## Expected behavior


## Sanitized request

Remove prompts with private data and remove all API keys.

```json

```

## Fallback chain or log snippet

Paste the fallback chain from the response or the relevant dashboard/server log lines.

```text

```

## Local checks

- [ ] The provider has at least one active/alive upstream key.
- [ ] The requested model exists in the provider model list or combo.
- [ ] The client request uses an AI Router local API key, not an upstream provider key.
- [ ] I removed secrets before posting.
