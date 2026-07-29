"""
Reasoning Content Middleware for Thinking Models

DeepSeek V4 Pro and similar thinking models require `reasoning_content` field
to be passed back in multi-turn conversations. This middleware:

1. Captures reasoning_content from streaming responses
2. Stores it per conversation (keyed by message signature)
3. Auto-injects missing reasoning_content into assistant messages

Without this, Hermes and other OpenAI-compatible clients fail on turn 2+
because they don't know to preserve the reasoning_content field.
"""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Cache reasoning_content per conversation
# Key: hash(assistant_message_content)
# Value: {reasoning: str, timestamp: float}
_reasoning_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

# Cache config
MAX_CACHE_SIZE = 10000
MAX_CACHE_AGE_SECONDS = 86400  # 24 hours
THINKING_MODELS = [
    "deepseek-v4-pro",
    "deepseek-v4",
    "deepseek-reasoner",
    "deepseek-r1",
    "deepseek-chat",
]


def _message_signature(content: str) -> str:
    """Generate cache key from assistant message content."""
    if not content:
        return ""
    # Use first 1000 chars + last 500 chars to handle long messages
    sample = content[:1000] + content[-500:] if len(content) > 1500 else content
    return hashlib.sha256(sample.encode()).hexdigest()[:16]


def _prune_cache():
    """Remove expired and oldest entries."""
    now = time.time()
    # Remove expired
    expired = [k for k, v in _reasoning_cache.items() if now - v["timestamp"] > MAX_CACHE_AGE_SECONDS]
    for key in expired:
        del _reasoning_cache[key]
    
    # Remove oldest if over limit
    while len(_reasoning_cache) > MAX_CACHE_SIZE:
        _reasoning_cache.popitem(last=False)


def store_reasoning(content: str, reasoning: str):
    """Store reasoning_content for a given assistant message."""
    if not content or not reasoning:
        return
    
    key = _message_signature(content)
    if not key:
        return
    
    _reasoning_cache[key] = {
        "reasoning": reasoning,
        "timestamp": time.time(),
    }
    _prune_cache()
    logger.debug(f"Stored reasoning for message {key[:8]}... ({len(reasoning)} chars)")


def retrieve_reasoning(content: str) -> str | None:
    """Retrieve stored reasoning_content for a given assistant message."""
    if not content:
        return None
    
    key = _message_signature(content)
    if not key:
        return None
    
    cached = _reasoning_cache.get(key)
    if not cached:
        return None
    
    # Check age
    if time.time() - cached["timestamp"] > MAX_CACHE_AGE_SECONDS:
        del _reasoning_cache[key]
        return None
    
    return cached["reasoning"]


def is_thinking_model(model: str) -> bool:
    """Check if model requires reasoning_content in message history."""
    if not model:
        return False
    model_lower = model.lower()
    return any(thinking in model_lower for thinking in THINKING_MODELS)


def inject_reasoning_content(body: dict, model: str):
    """
    Auto-inject missing reasoning_content into assistant messages.
    
    Called in _prepare_upstream() before forwarding to xstx.
    """
    if not is_thinking_model(model):
        return
    
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return
    
    injected_count = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        # Only process assistant messages without reasoning_content
        if msg.get("role") != "assistant":
            continue
        
        if msg.get("reasoning_content"):
            continue  # Already has reasoning
        
        # Try to retrieve from cache
        content = msg.get("content")
        if isinstance(content, str) and content:
            reasoning = retrieve_reasoning(content)
            if reasoning:
                msg["reasoning_content"] = reasoning
                injected_count += 1
                logger.debug(f"Injected reasoning for assistant message ({len(reasoning)} chars)")
            else:
                # Fallback: empty string to satisfy API requirement
                # (better than 400 error, but reasoning is lost)
                msg["reasoning_content"] = ""
                logger.warning(f"No cached reasoning found for assistant message, using empty string")
        elif not content:
            # Empty content assistant message (e.g., tool_calls only)
            msg["reasoning_content"] = ""
    
    if injected_count > 0:
        logger.info(f"Injected reasoning_content into {injected_count} assistant message(s)")


def capture_reasoning_from_chunks(chunks: list[dict]) -> dict[str, str]:
    """
    Extract reasoning_content and content from streaming chunks.
    
    Returns: {"content": accumulated_content, "reasoning": accumulated_reasoning}
    """
    content_buffer = []
    reasoning_buffer = []
    
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        
        choices = chunk.get("choices", [])
        if not isinstance(choices, list) or not choices:
            continue
        
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            continue
        
        # Accumulate content
        content = delta.get("content")
        if isinstance(content, str):
            content_buffer.append(content)
        
        # Accumulate reasoning_content
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            reasoning_buffer.append(reasoning)
    
    return {
        "content": "".join(content_buffer),
        "reasoning": "".join(reasoning_buffer),
    }


def get_cache_stats() -> dict:
    """Return cache statistics for monitoring."""
    return {
        "size": len(_reasoning_cache),
        "max_size": MAX_CACHE_SIZE,
        "max_age_seconds": MAX_CACHE_AGE_SECONDS,
    }
