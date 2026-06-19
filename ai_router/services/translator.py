import hashlib
import json
import logging
import time

from .upstream import provider_format

logger = logging.getLogger(__name__)


def request_needs_tools(body: dict | None):
    if not isinstance(body, dict):
        return False
    tools = body.get("tools")
    functions = body.get("functions")
    if isinstance(tools, list) and tools:
        return True
    if isinstance(functions, list) and functions:
        return True
    tool_choice = body.get("tool_choice")
    function_call = body.get("function_call")
    if tool_choice and tool_choice != "none":
        return True
    if function_call and function_call != "none":
        return True
    return False


def tool_flow_stats(body: dict):
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return {
            "messages": 0,
            "tools_declared": 0,
            "assistant_tool_calls": 0,
            "tool_results": 0,
            "missing_tool_call_id": 0,
            "orphan_tool_results": 0,
        }

    pending = []
    stats = {
        "messages": len(messages),
        "tools_declared": len(body.get("tools") or body.get("functions") or []),
        "assistant_tool_calls": 0,
        "tool_results": 0,
        "missing_tool_call_id": 0,
        "orphan_tool_results": 0,
    }
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for tool_call in msg.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                stats["assistant_tool_calls"] += 1
                tool_call_id = tool_call.get("id")
                if tool_call_id:
                    pending.append(tool_call_id)
        elif msg.get("role") == "tool":
            stats["tool_results"] += 1
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                stats["missing_tool_call_id"] += 1
                if not pending:
                    stats["orphan_tool_results"] += 1
            elif tool_call_id in pending:
                pending.remove(tool_call_id)
            else:
                stats["orphan_tool_results"] += 1
    return stats


def normalize_messages(body: dict, provider: dict | None = None, model: str = ""):
    messages = body.get("messages")
    if not isinstance(messages, list):
        return tool_flow_stats(body)

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        content = msg.get("content")
        if content == "":
            msg["content"] = " "
            content = msg["content"]

        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text") == "":
                    part["text"] = " "
    return tool_flow_stats(body)

def openai_to_anthropic(body: dict):
    messages = body.get("messages", [])
    system_parts = []
    converted = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            text = content_to_text(msg.get("content"))
            if text:
                system_parts.append(text)
            continue

        blocks = anthropic_content_blocks(msg)
        if not blocks:
            continue

        converted_role = "user" if role in ("user", "tool") else "assistant"
        if converted and converted[-1]["role"] == converted_role and role != "tool":
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": converted_role, "content": blocks})

    result = {
        "model": body.get("model", "claude-3-haiku-20240307"),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 16384,
        "messages": converted,
    }
    if system_parts:
        result["system"] = "\n".join(system_parts)
    if body.get("temperature") is not None:
        result["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        result["top_p"] = body["top_p"]
    if body.get("stream"):
        result["stream"] = True
    if body.get("tools"):
        result["tools"] = openai_tools_to_anthropic(body["tools"])
    if body.get("tool_choice"):
        result["tool_choice"] = openai_tool_choice_to_anthropic(body["tool_choice"])
    if body.get("response_format"):
        json_instruction = json_mode_instruction(body["response_format"])
        if json_instruction:
            result["system"] = "\n".join([p for p in (result.get("system"), json_instruction) if p])
    return result


def anthropic_content_blocks(msg: dict):
    role = msg.get("role")
    content = msg.get("content")
    blocks = []

    if role == "tool":
        return [{
            "type": "tool_result",
            "tool_use_id": msg.get("tool_call_id") or msg.get("id") or "tool_call",
            "content": content_to_text(content) or " ",
        }]

    tool_calls = msg.get("tool_calls") or []
    suppress_text = role == "assistant" and bool(tool_calls)

    if not suppress_text:
        if isinstance(content, str):
            if content:
                blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text" and part.get("text"):
                    blocks.append({"type": "text", "text": part["text"]})
                elif part_type == "image_url":
                    image_url = part.get("image_url") or {}
                    url = image_url.get("url")
                    if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
                        media_type, data = url[5:].split(";base64,", 1)
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
                    elif isinstance(url, str) and url:
                        blocks.append({"type": "image", "source": {"type": "url", "url": url}})
                elif part_type in ("tool_result", "tool_use", "image"):
                    blocks.append(part)

    if role == "assistant":
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function") or {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or f"call_{len(blocks)}",
                "name": function.get("name") or tc.get("name") or "tool",
                "input": try_json(function.get("arguments")),
            })

    return blocks

def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text") or "")
                elif "content" in part:
                    parts.append(str(part.get("content") or ""))
            elif part is not None:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def try_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except Exception:
            return value
    return value or {}


def stable_suffix(value):
    text = json.dumps(value or {}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:8]


def openai_tools_to_anthropic(tools):
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") and tool.get("type") != "function":
            converted.append(tool)
            continue
        function = tool.get("function") or tool
        converted.append({
            "name": function.get("name") or "tool",
            "description": function.get("description") or "",
            "input_schema": function.get("parameters") or function.get("input_schema") or {"type": "object", "properties": {}},
        })
    return converted


def openai_tool_choice_to_anthropic(choice):
    if choice in (None, "auto", "none"):
        return {"type": "auto"}
    if choice == "required":
        return {"type": "any"}
    if isinstance(choice, dict):
        function = choice.get("function") or {}
        if function.get("name"):
            return {"type": "tool", "name": function["name"]}
        if choice.get("type") in ("auto", "any", "tool"):
            return choice
    return {"type": "auto"}


def json_mode_instruction(response_format):
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") == "json_object":
        return "Respond only with valid JSON."
    if response_format.get("type") == "json_schema":
        schema = response_format.get("json_schema", {}).get("schema")
        if schema:
            return "Respond only with valid JSON matching this schema:\n" + json.dumps(schema, ensure_ascii=False)
    return None


def normalize_chat_response(data: dict, provider: dict, model: str, request_body: dict | None = None):
    if not isinstance(data, dict):
        return data

    # Convert Anthropic-native response to OpenAI format.
    # Check BOTH provider_format AND actual data shape — some providers are routed
    # via /v1/chat/completions but still return Anthropic-native responses (type=message).
    is_anthropic_response = data.get("type") == "message" and not isinstance(data.get("choices"), list)
    if is_anthropic_response:
        return anthropic_to_openai_response(data, model)

    if isinstance(data.get("choices"), list):
        data.setdefault("id", f"chatcmpl-{int(time.time() * 1000)}")
        data.setdefault("object", "chat.completion")
        data.setdefault("created", int(time.time()))
        data.setdefault("model", model)
        for choice in data["choices"]:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                message.setdefault("role", "assistant")
                if message.get("content") is None and not message.get("tool_calls"):
                    message["content"] = ""
                synthesize_forced_tool_call(choice, message, request_body)
        return data

    return data


def response_tool_stats(data: dict):
    stats = {
        "choices": 0,
        "tool_calls": 0,
        "finish_reason": "",
        "has_content": False,
    }
    if not isinstance(data, dict):
        return stats
    choices = data.get("choices")
    if isinstance(choices, list):
        stats["choices"] = len(choices)
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if not stats["finish_reason"] and choice.get("finish_reason"):
                stats["finish_reason"] = str(choice.get("finish_reason"))
            message = choice.get("message") or choice.get("delta") or {}
            if not isinstance(message, dict):
                continue
            if message.get("content"):
                stats["has_content"] = True
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                stats["tool_calls"] += len(tool_calls)
    elif data.get("type") == "message":
        content = data.get("content") or []
        stats["choices"] = 1
        stats["finish_reason"] = str(data.get("stop_reason") or "")
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                stats["has_content"] = True
            if part.get("type") == "tool_use":
                stats["tool_calls"] += 1
    return stats


def forced_tool_name(request_body: dict | None):
    if not isinstance(request_body, dict):
        return None
    choice = request_body.get("tool_choice") or request_body.get("function_call")
    if isinstance(choice, dict):
        function = choice.get("function") or {}
        name = function.get("name") or choice.get("name")
        if name:
            return name
    if isinstance(choice, str) and choice not in ("auto", "none", "required"):
        return choice
    tools = request_body.get("tools") or request_body.get("functions") or []
    if len(tools) == 1 and isinstance(tools[0], dict):
        function = tools[0].get("function") or tools[0]
        return function.get("name")
    return None


def jsonish_tool_arguments(content):
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        json.loads(text)
    except Exception:
        return None
    return text


def synthesize_forced_tool_call(choice: dict, message: dict, request_body: dict | None):
    if message.get("tool_calls"):
        return
    tool_name = forced_tool_name(request_body)
    if not tool_name:
        return
    arguments = jsonish_tool_arguments(message.get("content"))
    if arguments is None:
        return
    message["tool_calls"] = [{
        "id": f"call_{int(time.time() * 1000)}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": arguments,
        },
    }]
    message["content"] = None
    choice["finish_reason"] = "tool_calls"


def anthropic_to_openai_response(data: dict, model: str):
    text_parts = []
    tool_calls = []
    for idx, part in enumerate(data.get("content") or []):
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_parts.append(part.get("text") or "")
        elif part_type == "tool_use":
            tool_calls.append({
                "id": part.get("id") or f"call_{idx}",
                "type": "function",
                "function": {
                    "name": part.get("name") or "tool",
                    "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                },
            })

    usage = data.get("usage") or {}
    content = "".join(text_parts)
    message = {
        "role": "assistant",
        "content": None if tool_calls else content,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_reason = data.get("stop_reason")
    finish_reason = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }.get(stop_reason, stop_reason or "stop")
    if tool_calls:
        finish_reason = "tool_calls"

    return {
        "id": data.get("id") or f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }

def responses_input_to_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("input_text") or item.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return str(content)


def responses_to_chat_request(body: dict):
    chat = {
        "model": body.get("model", ""),
        "messages": [],
    }
    if body.get("stream") is not None:
        chat["stream"] = bool(body.get("stream"))
    if body.get("temperature") is not None:
        chat["temperature"] = body.get("temperature")
    if body.get("top_p") is not None:
        chat["top_p"] = body.get("top_p")
    if body.get("max_output_tokens") is not None:
        chat["max_completion_tokens"] = body.get("max_output_tokens")
    elif body.get("max_tokens") is not None:
        chat["max_tokens"] = body.get("max_tokens")

    instructions = body.get("instructions")
    if instructions:
        chat["messages"].append({"role": "system", "content": responses_input_to_text(instructions)})

    input_value = body.get("input")
    if isinstance(input_value, str):
        chat["messages"].append({"role": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if not isinstance(item, dict):
                chat["messages"].append({"role": "user", "content": responses_input_to_text(item)})
                continue
            item_type = item.get("type")
            role = item.get("role") or "user"
            if role == "developer":
                role = "system"
            if item_type in ("message", None):
                chat["messages"].append({
                    "role": role,
                    "content": responses_input_to_text(item.get("content")),
                })
            elif item_type == "function_call_output":
                chat["messages"].append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id") or "",
                    "content": responses_input_to_text(item.get("output")),
                })
            elif item_type == "function_call":
                chat["messages"].append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id") or item.get("id") or f"call_{stable_suffix(item)}",
                        "type": "function",
                        "function": {
                            "name": item.get("name") or "tool",
                            "arguments": item.get("arguments") if isinstance(item.get("arguments"), str) else json.dumps(item.get("arguments") or {}, ensure_ascii=False),
                        },
                    }],
                })
    elif body.get("messages"):
        chat["messages"] = body.get("messages") or []

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        chat_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and "function" not in tool:
                chat_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name") or "tool",
                        "description": tool.get("description") or "",
                        "parameters": tool.get("parameters") or {},
                    },
                })
            else:
                chat_tools.append(tool)
        chat["tools"] = chat_tools
    if body.get("tool_choice") is not None:
        chat["tool_choice"] = body.get("tool_choice")
    if body.get("response_format") is not None:
        chat["response_format"] = body.get("response_format")
    return chat


def chat_to_responses_response(data: dict, request_body: dict | None = None):
    model = data.get("model") or (request_body or {}).get("model") or ""
    created = data.get("created") or int(time.time())
    response_id = data.get("id") or f"resp_{created}"
    output = []

    choices = data.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            continue
        message_tool_calls = [tc for tc in (message.get("tool_calls") or []) if isinstance(tc, dict)]
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            call_id = tool_call.get("id") or f"call_{stable_suffix(tool_call)}"
            item_id = call_id if str(call_id).startswith("fc_") else f"fc_{call_id}"
            output.append({
                "id": item_id,
                "type": "function_call",
                "call_id": call_id,
                "name": function.get("name") or "tool",
                "arguments": function.get("arguments") or "{}",
                "status": "completed",
            })
        content = message.get("content")
        finish_reason = choice.get("finish_reason")
        suppress_tool_text = bool(message_tool_calls) and finish_reason in ("tool_calls", "tool_use")
        if content and not suppress_tool_text:
            output.append({
                "id": f"msg_{stable_suffix(content)}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": content if isinstance(content, str) else content_to_text(content),
                    "annotations": [],
                }],
            })

    usage = data.get("usage") or {}
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens") or input_tokens + output_tokens,
        },
    }


def chat_response_to_history_messages(data: dict):
    messages = []
    if not isinstance(data, dict):
        return messages
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        history_message = {"role": message.get("role") or "assistant"}
        if "content" in message:
            history_message["content"] = message.get("content")
        if message.get("tool_calls"):
            history_message["tool_calls"] = message.get("tool_calls")
        if history_message.get("content") is not None or history_message.get("tool_calls"):
            messages.append(history_message)
    return messages


