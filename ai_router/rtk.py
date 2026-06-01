"""Small RTK-style compression for long tool outputs.

This intentionally stays conservative: only tool-result-like messages are
compressed, so normal user prompts and assistant answers are left untouched.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


MIN_TOOL_CHARS = 12000
HEAD_LINES = 200
TAIL_LINES = 100
MAX_LINE_CHARS = 1200


@dataclass
class RtkStats:
    changed: bool = False
    messages_seen: int = 0
    messages_compressed: int = 0
    chars_before: int = 0
    chars_after: int = 0

    @property
    def saved_chars(self) -> int:
        return max(0, self.chars_before - self.chars_after)


def compress_request_body(body: dict[str, Any], enabled: bool = True) -> tuple[dict[str, Any], RtkStats]:
    """Return a possibly-compressed copy of an OpenAI-compatible request body."""
    stats = RtkStats()
    if not enabled or not isinstance(body, dict):
        return body, stats

    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, stats

    next_body = deepcopy(body)
    next_messages = next_body.get("messages", [])

    for msg in next_messages:
        if not isinstance(msg, dict) or not _is_tool_message(msg):
            continue
        stats.messages_seen += 1
        before = _message_text_len(msg)
        stats.chars_before += before
        if before < MIN_TOOL_CHARS:
            stats.chars_after += before
            continue

        changed = _compress_message_content(msg)
        after = _message_text_len(msg)
        stats.chars_after += after
        if changed and after < before:
            stats.changed = True
            stats.messages_compressed += 1

    if not stats.changed:
        return body, stats
    return next_body, stats


def _is_tool_message(msg: dict[str, Any]) -> bool:
    if msg.get("role") == "tool":
        return True
    content = msg.get("content")
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "tool_result" for part in content)
    return False


def _message_text_len(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                value = part.get("content") or part.get("text") or ""
                if isinstance(value, str):
                    total += len(value)
        return total
    return 0


def _compress_message_content(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    if isinstance(content, str):
        compressed = compress_tool_output(content)
        if compressed != content:
            msg["content"] = compressed
            return True
        return False

    if isinstance(content, list):
        changed = False
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            value = part.get("content")
            if not isinstance(value, str):
                continue
            compressed = compress_tool_output(value)
            if compressed != value:
                part["content"] = compressed
                changed = True
        return changed

    return False


def compress_tool_output(text: str) -> str:
    if len(text) < MIN_TOOL_CHARS:
        return text

    kind = detect_output_kind(text)
    lines = text.splitlines()
    if kind == "git_diff":
        compressed = _compress_git_diff(lines)
    elif kind == "grep":
        compressed = _compress_grep(lines)
    elif kind == "listing":
        compressed = _compress_listing(lines)
    elif kind == "build_log":
        compressed = _compress_build_log(lines)
    else:
        compressed = _compress_generic(lines)

    if len(compressed) >= len(text):
        return text
    return (
        f"[ai-router RTK compressed tool output: kind={kind}, "
        f"original_chars={len(text)}, compressed_chars={len(compressed)}]\n"
        f"{compressed}"
    )


def detect_output_kind(text: str) -> str:
    sample = text[:10000]
    if re.search(r"^diff --git ", sample, re.MULTILINE) or re.search(r"^(---|\+\+\+) ", sample, re.MULTILINE):
        return "git_diff"
    if re.search(r"^[^:\n]+:\d+:", sample, re.MULTILINE):
        return "grep"
    if re.search(r"\b(error|failed|traceback|exception|warning)\b", sample, re.IGNORECASE):
        return "build_log"
    lines = sample.splitlines()
    if len(lines) > 100 and _looks_like_listing(lines):
        return "listing"
    return "generic"


def _looks_like_listing(lines: list[str]) -> bool:
    hits = 0
    for line in lines[:200]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[\-dl][rwx\-]{9}\s+", stripped):
            hits += 1
        elif re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}\s+", stripped):
            hits += 1
        elif re.search(r"\.(py|js|jsx|ts|tsx|json|md|txt|css|html|mjs|pyc)$", stripped):
            hits += 1
    return hits >= 20


def _clean_line(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " ... [line truncated]"


def _head_tail(lines: list[str], head: int = HEAD_LINES, tail: int = TAIL_LINES) -> str:
    if len(lines) <= head + tail:
        return "\n".join(_clean_line(line) for line in lines)
    omitted = len(lines) - head - tail
    out = [_clean_line(line) for line in lines[:head]]
    out.append(f"... [omitted {omitted} middle lines] ...")
    out.extend(_clean_line(line) for line in lines[-tail:])
    return "\n".join(out)


def _compress_generic(lines: list[str]) -> str:
    return _head_tail(lines)


def _compress_listing(lines: list[str]) -> str:
    return _head_tail(lines, head=100, tail=30)


def _compress_grep(lines: list[str]) -> str:
    if len(lines) <= 400:
        return "\n".join(_clean_line(line) for line in lines)
    buckets: dict[str, int] = {}
    selected = []
    for line in lines:
        path = line.split(":", 1)[0] if ":" in line else "<unknown>"
        buckets[path] = buckets.get(path, 0) + 1
        if buckets[path] <= 20 and len(selected) < 400:
            selected.append(_clean_line(line))
    summary = ", ".join(f"{path}={count}" for path, count in list(buckets.items())[:40])
    return f"[grep summary: files={len(buckets)}, matches={len(lines)}]\n{summary}\n\n" + "\n".join(selected)


def _compress_git_diff(lines: list[str]) -> str:
    files = []
    current = []
    current_name = None
    for line in lines:
        if line.startswith("diff --git "):
            if current:
                files.append((current_name or "unknown", current))
            current = [line]
            current_name = line.split(" b/", 1)[-1] if " b/" in line else line
        else:
            current.append(line)
    if current:
        files.append((current_name or "unknown", current))

    out = [f"[git diff summary: files={len(files)}]"]
    for name, chunk in files[:30]:
        adds = sum(1 for line in chunk if line.startswith("+") and not line.startswith("+++"))
        dels = sum(1 for line in chunk if line.startswith("-") and not line.startswith("---"))
        out.append(f"\n--- {name} (+{adds}/-{dels}) ---")
        out.append(_head_tail(chunk, head=35, tail=15))
    if len(files) > 30:
        out.append(f"\n... [omitted {len(files) - 30} changed files] ...")
    return "\n".join(out)


def _compress_build_log(lines: list[str]) -> str:
    important = [
        _clean_line(line)
        for line in lines
        if re.search(r"\b(error|failed|traceback|exception|warning|fatal)\b", line, re.IGNORECASE)
    ]
    if important:
        important = important[-200:]
        return (
            f"[build log summary: total_lines={len(lines)}, important_lines={len(important)}]\n"
            + "\n".join(important)
            + "\n\n[tail]\n"
            + _head_tail(lines, head=0, tail=120)
        )
    return _head_tail(lines)
