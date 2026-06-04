# Contributing

Thanks for helping improve AI Router. This project is a local AI gateway for
OpenAI-compatible clients, Anthropic-compatible clients, custom upstream
providers, combos, fallback routing, and streaming.

## Before opening an issue

- Search existing issues first.
- Remove all real API keys, local `ar-...` keys, cookies, tokens, database
  files, and private prompts from logs or screenshots.
- Include the endpoint, provider type, model, streaming mode, and fallback
  details when reporting routing or provider problems.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd web
npm install
cd ..
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd web
npm install
cd ..
```

## Checks before submitting

Run the relevant checks before opening a pull request:

```bash
python -m py_compile $(find . -name "*.py" -not -path "./web/node_modules/*" -not -path "./.venv/*")
npm --prefix web run lint
python scripts/build_static.py
```

If the frontend changed, rebuild static assets with:

```bash
python scripts/build_static.py
```

Commit the updated `ai_router/static` files with the source changes.

## Routing behavior to preserve

- OpenAI-compatible clients use `/v1/chat/completions`.
- Anthropic-compatible clients use `/v1/messages`.
- Explicit unknown model names should not silently route to a random provider.
- Combos should resolve before direct provider/model routing.
- Provider prefixes are optional and should not be required by default.

## Pull request guidance

- Keep changes focused.
- Do not commit secrets, logs with keys, local databases, `.env`, `node_modules`,
  `web/dist`, or package lock files if they are ignored.
- Update README or SKILLS.md when behavior, endpoints, setup, or agent guidance
  changes.
- Mention manual tests for provider routing, streaming, fallback, or dashboard
  changes.
