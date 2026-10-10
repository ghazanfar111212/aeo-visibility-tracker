"""
Answer engines. Each engine takes a prompt and returns the answer text plus any
source URLs it cited. Plain HTTP calls, no SDKs, so the only dependency is requests.
"""

import hashlib
import os
import random
import time

import requests


class EngineError(Exception):
    pass


def _post(url, headers, payload, retries=3, timeout=90):
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise EngineError(str(e))
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 529) and attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code != 200:
            raise EngineError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise EngineError("No response after retries")


def _key(name):
    value = os.environ.get(name)
    if not value:
        raise EngineError(f"{name} is not set. Add it to your .env file.")
    return value


def ask_openai(prompt, model):
    data = _post(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"},
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
    )
    return data["choices"][0]["message"]["content"], []


def ask_anthropic(prompt, model):
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": _key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]},
    )
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return text, []


def ask_perplexity(prompt, model):
    data = _post(
        "https://api.perplexity.ai/chat/completions",
        {"Authorization": f"Bearer {_key('PERPLEXITY_API_KEY')}"},
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
    )
    text = data["choices"][0]["message"]["content"]
    citations = data.get("citations") or [
        r.get("url") for r in data.get("search_results", []) if r.get("url")
    ]
    return text, citations


def ask_gemini(prompt, model):
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": _key("GEMINI_API_KEY")},
        {"contents": [{"parts": [{"text": prompt}]}]},
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts), []


def ask_mock(prompt, model, entities=None, engine_name="mock"):
    """Fake but realistic answers for demos and testing, no API keys needed.
    The same prompt and engine always produce the same answer."""
    seed = int(hashlib.md5(f"{engine_name}|{prompt}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    names = [e["name"] for e in (entities or [])]
    rng.shuffle(names)
    picked = [n for n in names if rng.random() < 0.6][:5] or names[:2]
    lines = [f"Here are some good options for \"{prompt}\":", ""]
    for i, name in enumerate(picked, 1):
        lines.append(f"{i}. **{name}**: a solid choice depending on your budget and team size.")
    lines += ["", "The right pick depends on your needs, integrations and budget."]
    cites = [f"https://www.example-review-site-{rng.randint(1, 4)}.com/best-tools"] if engine_name == "perplexity" else []
    return "\n".join(lines), cites


ENGINES = {
    "openai": ask_openai,
    "anthropic": ask_anthropic,
    "perplexity": ask_perplexity,
    "gemini": ask_gemini,
}
