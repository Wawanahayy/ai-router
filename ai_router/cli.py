"""Small command-line helpers for AI Router."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


DEFAULT_URL = "http://localhost:32128"


def _quote(value: str, shell: str) -> str:
    if shell == "powershell":
        return '"' + value.replace('"', '`"') + '"'
    if shell == "cmd":
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _claude_env(args: argparse.Namespace) -> dict[str, str]:
    env = {
        "ANTHROPIC_BASE_URL": args.url,
        "ANTHROPIC_AUTH_TOKEN": args.api_key,
    }
    if args.model:
        env["ANTHROPIC_MODEL"] = args.model
    if args.model_discovery:
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    if args.disable_experimental_betas:
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    return env


def cmd_claude_env(args: argparse.Namespace) -> int:
    env = _claude_env(args)
    shell = args.shell
    for key, value in env.items():
        if shell == "powershell":
            print(f"$env:{key}={_quote(value, shell)}")
        elif shell == "cmd":
            print(f"set {key}={_quote(value, shell)}")
        else:
            print(f"export {key}={_quote(value, shell)}")
    print("claude")
    return 0


def cmd_claude_run(args: argparse.Namespace) -> int:
    claude = shutil.which(args.claude_bin)
    if not claude:
        print(f"Claude CLI not found: {args.claude_bin}", file=sys.stderr)
        print("Install Claude Code first, then rerun this command.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.update(_claude_env(args))
    command = [claude]
    if args.prompt:
        command.extend(["-p", args.prompt])
    return subprocess.run(command, env=env, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Router CLI helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_claude_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default=os.getenv("ANTHROPIC_BASE_URL", DEFAULT_URL), help="AI Router root URL")
        p.add_argument("--api-key", default=os.getenv("ANTHROPIC_AUTH_TOKEN", "ar-your-local-key"), help="AI Router local API key")
        p.add_argument("--model", default=os.getenv("ANTHROPIC_MODEL", ""), help="AI Router model alias")
        p.add_argument("--model-discovery", action="store_true", help="Enable Claude Code gateway model discovery")
        p.add_argument("--disable-experimental-betas", action="store_true", help="Ask Claude Code to omit experimental beta headers")

    env_cmd = sub.add_parser("claude-env", help="Print shell commands for Claude Code")
    add_claude_options(env_cmd)
    env_cmd.add_argument("--shell", choices=("bash", "powershell", "cmd"), default="bash")
    env_cmd.set_defaults(func=cmd_claude_env)

    run_cmd = sub.add_parser("claude-run", help="Run Claude Code through AI Router")
    add_claude_options(run_cmd)
    run_cmd.add_argument("--claude-bin", default="claude", help="Claude CLI executable name/path")
    run_cmd.add_argument("--prompt", default="", help="Optional non-interactive prompt passed with claude -p")
    run_cmd.set_defaults(func=cmd_claude_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
