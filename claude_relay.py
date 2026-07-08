#!/usr/bin/env python3
"""
Anthropic Messages API relay → OpenAI Chat Completions.

claude CLI sends POST /v1/messages (Anthropic format).
This relay translates to POST /v1/chat/completions (OpenAI format)
and streams responses back in Anthropic SSE format.

Supports: streaming, tool_use/function_call, thinking blocks (stripped).
"""
import argparse, http.server, json, os, time, uuid
try:
    import requests as _req; USE_REQUESTS = True
except ImportError:
    USE_REQUESTS = False

UPSTREAM_BASE = os.environ.get("RELAY_UPSTREAM", "https://dashscope.aliyuncs.com/compatible-mode/v1")
UPSTREAM_CHAT = f"{UPSTREAM_BASE.rstrip('/')}/chat/completions"
# RELAY_MODEL overrides the model the client sends (client may send a dummy Claude model name)
RELAY_MODEL = os.environ.get("RELAY_MODEL", "")
# RELAY_API_KEY: real upstream key. Falls back to Authorization header from client.
RELAY_API_KEY = os.environ.get("RELAY_API_KEY", "")


# ── Anthropic → OpenAI request conversion ──────────────────────────────────

def _anthropic_content_to_oai(content):
    """Convert Anthropic content blocks to OpenAI message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            btype = blk.get("type", "text")
            if btype == "text":
                parts.append(blk.get("text", ""))
            elif btype == "thinking":
                pass  # Strip thinking blocks from history
            elif btype == "tool_use":
                pass  # Handled separately as tool_calls
        return "\n".join(p for p in parts if p) or None
    return str(content) if content else None


def anthropic_to_oai(anthropic_req):
    system = anthropic_req.get("system")
    messages_in = anthropic_req.get("messages", [])
    tools_in = anthropic_req.get("tools", [])
    stream = anthropic_req.get("stream", False)
    max_tokens = anthropic_req.get("max_tokens") or 8192
    temperature = anthropic_req.get("temperature")
    # Use RELAY_MODEL if set (client sends dummy Claude model name for CLI validation)
    model = RELAY_MODEL or anthropic_req.get("model", "")

    oai_messages = []
    if system:
        if isinstance(system, list):
            sys_text = " ".join(b.get("text", "") for b in system if b.get("type") == "text")
        else:
            sys_text = str(system)
        if sys_text.strip():
            oai_messages.append({"role": "system", "content": sys_text})

    for msg in messages_in:
        role = msg.get("role", "")
        content = msg.get("content", [])

        if role == "user":
            if isinstance(content, list):
                # Check for tool_result blocks
                tool_results = [b for b in content if b.get("type") == "tool_result"]
                text_blocks = [b for b in content if b.get("type") == "text"]
                if tool_results:
                    for tr in tool_results:
                        tr_content = tr.get("content", "")
                        if isinstance(tr_content, list):
                            tr_text = "\n".join(
                                b.get("text", "") for b in tr_content if b.get("type") == "text"
                            )
                        else:
                            tr_text = str(tr_content) if tr_content else ""
                        oai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": tr_text,
                        })
                    if text_blocks:
                        texts = [b.get("text", "") for b in text_blocks if b.get("text")]
                        if texts:
                            oai_messages.append({"role": "user", "content": "\n".join(texts)})
                else:
                    text = _anthropic_content_to_oai(content)
                    oai_messages.append({"role": "user", "content": text or ""})
            else:
                oai_messages.append({"role": "user", "content": str(content) if content else ""})

        elif role == "assistant":
            if isinstance(content, list):
                text_parts = []
                tool_uses = []
                for blk in content:
                    btype = blk.get("type", "text")
                    if btype == "text":
                        text_parts.append(blk.get("text", ""))
                    elif btype == "thinking":
                        pass  # Strip thinking
                    elif btype == "tool_use":
                        tool_uses.append(blk)
                msg_obj = {"role": "assistant"}
                combined_text = "\n".join(t for t in text_parts if t)
                msg_obj["content"] = combined_text or None
                if tool_uses:
                    msg_obj["tool_calls"] = [
                        {
                            "id": tu.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": tu.get("name", ""),
                                "arguments": json.dumps(tu.get("input", {})),
                            },
                        }
                        for tu in tool_uses
                    ]
                oai_messages.append(msg_obj)
            else:
                text = _anthropic_content_to_oai(content)
                oai_messages.append({"role": "assistant", "content": text or ""})

    # Convert tools
    oai_tools = []
    for t in (tools_in or []):
        oai_tools.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })

    oai_req = {
        "model": model,
        "messages": oai_messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if oai_tools:
        oai_req["tools"] = oai_tools
    if temperature is not None:
        oai_req["temperature"] = temperature
    return oai_req


# ── OpenAI → Anthropic response conversion ─────────────────────────────────

STOP_MAP_REV = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def oai_to_anthropic(oai_body, anthropic_model):
    """Convert OpenAI chat.completion response to Anthropic Messages response."""
    choice = (oai_body.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    text = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = STOP_MAP_REV.get(finish_reason, "end_turn")
    u = oai_body.get("usage", {})
    inp = u.get("prompt_tokens", 0)
    out = u.get("completion_tokens", 0)

    content_blocks = []
    if text:
        content_blocks.append({"type": "text", "text": text})
    for tc in tool_calls:
        fn = tc.get("function", {})
        args_str = fn.get("arguments", "{}")
        try:
            args = json.loads(args_str)
        except Exception:
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:8]}",
            "name": fn.get("name", ""),
            "input": args,
        })

    return {
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": anthropic_model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": inp, "output_tokens": out},
    }


class OAIStreamToAnthropic:
    """Convert streaming OpenAI SSE chunks to Anthropic SSE events."""
    def __init__(self, model, msg_id):
        self.model = model
        self.msg_id = msg_id
        self.text_block_started = False
        self.tool_blocks = {}  # index -> {"id","name","args_buf"}
        self.block_idx = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _sse(self, event, data):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def start(self):
        return self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.msg_id, "type": "message", "role": "assistant",
                "content": [], "model": self.model,
                "stop_reason": None, "stop_sequence": None,
                "usage": self.usage,
            },
        })

    def handle_chunk(self, chunk):
        """Yield Anthropic SSE string(s) for one OpenAI chunk."""
        events = []
        choices = chunk.get("choices") or []
        if not choices:
            # might be usage-only chunk
            if chunk.get("usage"):
                u = chunk["usage"]
                self.usage["input_tokens"] = u.get("prompt_tokens", 0)
                self.usage["output_tokens"] = u.get("completion_tokens", 0)
            return events

        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason")

        # Text delta
        text = delta.get("content", "")
        if text:
            if not self.text_block_started:
                self.text_block_started = True
                events.append(self._sse("content_block_start", {
                    "type": "content_block_start",
                    "index": self.block_idx,
                    "content_block": {"type": "text", "text": ""},
                }))
                self.block_idx += 1
            events.append(self._sse("content_block_delta", {
                "type": "content_block_delta",
                "index": self.block_idx - 1,
                "delta": {"type": "text_delta", "text": text},
            }))

        # Tool call deltas
        for tc_delta in (delta.get("tool_calls") or []):
            idx = tc_delta.get("index", 0)
            if idx not in self.tool_blocks:
                # New tool call block
                fn = tc_delta.get("function", {})
                tc_id = tc_delta.get("id") or f"toolu_{uuid.uuid4().hex[:8]}"
                name = fn.get("name", "")
                self.tool_blocks[idx] = {"id": tc_id, "name": name, "args_buf": "", "blk_idx": self.block_idx}
                self.block_idx += 1
                events.append(self._sse("content_block_start", {
                    "type": "content_block_start",
                    "index": self.tool_blocks[idx]["blk_idx"],
                    "content_block": {
                        "type": "tool_use",
                        "id": tc_id,
                        "name": name,
                        "input": {},
                    },
                }))
            fn_delta = tc_delta.get("function", {})
            args_frag = fn_delta.get("arguments", "")
            if args_frag:
                self.tool_blocks[idx]["args_buf"] += args_frag
                events.append(self._sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self.tool_blocks[idx]["blk_idx"],
                    "delta": {"type": "input_json_delta", "partial_json": args_frag},
                }))

        # Finish
        if finish_reason:
            # Close any open blocks
            for blk_data in self.tool_blocks.values():
                events.append(self._sse("content_block_stop", {
                    "type": "content_block_stop",
                    "index": blk_data["blk_idx"],
                }))
            if self.text_block_started:
                events.append(self._sse("content_block_stop", {
                    "type": "content_block_stop",
                    "index": self.block_idx - len(self.tool_blocks) - 1,
                }))
            stop_reason = STOP_MAP_REV.get(finish_reason, "end_turn")
            events.append(self._sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": self.usage["output_tokens"]},
            }))
            events.append(self._sse("message_stop", {"type": "message_stop"}))

        return events


# ── HTTP Handler ─────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json(200, {"status": "ok"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        anthropic_req = json.loads(self.rfile.read(n))
        stream = anthropic_req.get("stream", False)
        # response_model: keep the fake Claude model name in responses so the CLI doesn't complain
        response_model = anthropic_req.get("model", "")

        oai_req = anthropic_to_oai(anthropic_req)

        # Prefer RELAY_API_KEY (real DashScope key) over whatever the client sent
        upstream_auth = f"Bearer {RELAY_API_KEY}" if RELAY_API_KEY else self.headers.get("Authorization", "")
        hdrs = {"Authorization": upstream_auth, "Content-Type": "application/json"}

        if USE_REQUESTS:
            import requests
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                msg_id = f"msg_{uuid.uuid4().hex[:8]}"
                conv = OAIStreamToAnthropic(response_model, msg_id)
                try:
                    self.wfile.write(conv.start().encode()); self.wfile.flush()
                    with requests.post(UPSTREAM_CHAT, json=oai_req, headers=hdrs,
                                       stream=True, timeout=300) as r:
                        for line in r.iter_lines(decode_unicode=True):
                            if not line or not line.startswith("data:"):
                                continue
                            ds = line[5:].strip()
                            if ds == "[DONE]":
                                break
                            try:
                                chunk = json.loads(ds)
                            except Exception:
                                continue
                            for ev in conv.handle_chunk(chunk):
                                self.wfile.write(ev.encode()); self.wfile.flush()
                except Exception as e:
                    err_ev = f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                    try:
                        self.wfile.write(err_ev.encode()); self.wfile.flush()
                    except Exception:
                        pass
            else:
                try:
                    r = requests.post(UPSTREAM_CHAT, json=oai_req, headers=hdrs, timeout=120)
                    try:
                        body = r.json()
                    except Exception:
                        body = {"error": "non-json"}
                    if r.status_code == 200 and "choices" in body:
                        body = oai_to_anthropic(body, response_model)
                    self._json(r.status_code, body)
                except Exception as e:
                    self._json(500, {"error": str(e)})
        else:
            import urllib.request, urllib.error
            try:
                req_data = json.dumps(oai_req).encode()
                req_obj = urllib.request.Request(UPSTREAM_CHAT, req_data, hdrs, method="POST")
                resp = urllib.request.urlopen(req_obj, timeout=120)
                body = json.loads(resp.read())
                if "choices" in body:
                    body = oai_to_anthropic(body, response_model)
                self._json(200, body)
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read())
                except Exception:
                    body = {"error": str(e)}
                self._json(e.code, body)
            except Exception as e:
                self._json(500, {"error": str(e)})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=19181)
    a = p.parse_args()
    s = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print(f"[claude-relay] listening on 0.0.0.0:{a.port}", flush=True)
    s.serve_forever()
