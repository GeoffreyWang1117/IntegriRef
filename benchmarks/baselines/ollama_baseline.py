"""LLM baseline via Ollama Cloud.

Runs an open-source LLM (e.g., glm-4.6, qwen3-coder:480b, gpt-oss:120b) to
classify references as REAL or HALLUCINATED.

Two modes:
  --mode parametric  : knowledge-only classification (matches the existing
                       Gemini Flash zero-shot baseline)
  --mode tool        : the model can call a `lookup_crossref` tool to verify
                       the DOI before deciding (retrieval-augmented baseline,
                       the realistic LLM comparison)

The Ollama Cloud free tier covers glm-4.6, qwen3-coder:480b, gpt-oss:120b,
gemma3:27b, gemma4:31b, minimax-m2.5, nemotron-3-super. Premium models
(glm-5.1, deepseek-v3.2, kimi-k2) require an upgraded subscription.

Reproducibility: the parametric mode is deterministic at temperature=0 and
runs identically on a local Ollama install (`ollama run gpt-oss:120b`) for
researchers without cloud access; the tool mode requires Crossref API
access (free, no key).

Usage:
    python -m benchmarks.baselines.ollama_baseline \
        --model glm-4.6 --mode parametric \
        --output benchmarks/results/llm_baseline_glm46_parametric.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ollama_baseline")
logger.setLevel(logging.INFO)


OLLAMA_URL = os.environ.get("OLLAMA_URL", "https://ollama.com/api/chat")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
GOLDEN_FILE = Path(__file__).resolve().parent.parent / "data" / "golden_test_set.json"
RV_FILE = (Path(__file__).resolve().parent.parent / "data"
           / "hf_export" / "reference_verification.jsonl")


def is_deepseek(model: str) -> bool:
    """Models served by DeepSeek's official API rather than Ollama Cloud."""
    return model.startswith("deepseek-v4") or model.startswith("deepseek-r")


# ─── .env loader ──────────────────────────────────────────────────────────

def load_env():
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# ─── Datasets ─────────────────────────────────────────────────────────────

def load_golden_full() -> list[dict]:
    """Full 63-case golden set with original labels (no reclassification)."""
    with open(GOLDEN_FILE) as f:
        data = json.load(f)
    cases = []
    for item in data.get("retracted_papers", []):
        # retracted papers are "real" for halluc-vs-real binary task
        cases.append({**item, "category": "retracted", "ground_truth": "real"})
    for item in data.get("real_papers", []):
        cases.append({**item, "category": "real", "ground_truth": "real"})
    for item in data.get("llm_hallucinated", []):
        cases.append({**item, "category": "hallucinated", "ground_truth": "hallucinated"})
    for item in data.get("metadata_chimera", []):
        cases.append({**item, "category": "chimera", "ground_truth": "hallucinated"})
    return cases


def load_rv_sample(n: int, seed: int = 42) -> list[dict]:
    """Random sample of the reference_verification split."""
    import random
    rng = random.Random(seed)
    cases = []
    with open(RV_FILE) as f:
        for line in f:
            cases.append(json.loads(line))
    rng.shuffle(cases)
    # Assign ground_truth field
    out = []
    for c in cases[:n]:
        cat = c.get("category", "")
        gt = "hallucinated" if cat in ("hallucinated", "chimera") else "real"
        out.append({**c, "ground_truth": gt})
    return out


# ─── Prompts ──────────────────────────────────────────────────────────────

PARAMETRIC_PROMPT = """You are a reference verification expert. Given a bibliographic reference, determine whether it is a REAL published paper or a HALLUCINATED (fabricated) reference.

Reference:
- Title: {title}
- Authors: {authors}
- Year: {year}
- DOI: {doi}
- Venue: {venue}

Based ONLY on your knowledge (do not search the internet), classify this reference as:
- REAL: You are confident this is a genuine published paper
- HALLUCINATED: You believe this reference is fabricated or does not exist

Respond with exactly one word: REAL or HALLUCINATED."""


TOOL_PROMPT = """You are a reference verification expert. You have access to a `lookup_crossref` tool that queries the Crossref API by DOI. Use the tool ONCE to verify the reference, then decide:
- REAL: the DOI resolves and title/authors match
- HALLUCINATED: the DOI does not resolve, or resolves to a clearly unrelated record

Reference to verify:
- Title: {title}
- Authors: {authors}
- Year: {year}
- DOI: {doi}
- Venue: {venue}

Call the tool, then answer with exactly one word: REAL or HALLUCINATED."""


# ─── Ollama call ──────────────────────────────────────────────────────────

def ollama_chat(model: str, messages: list,
                tools: Optional[list] = None,
                num_predict: int = 32,
                api_key: Optional[str] = None,
                max_retries: int = 5) -> dict:
    """Unified chat call. Routes to DeepSeek (OpenAI-compatible) for
    deepseek-v4-* models and to Ollama Cloud otherwise. Returns a dict
    in Ollama's response shape so downstream code can be uniform.
    """
    if is_deepseek(model):
        return _deepseek_chat(model, messages, tools, num_predict,
                              api_key, max_retries)
    return _ollama_chat(model, messages, tools, num_predict,
                        api_key, max_retries)


def _ollama_chat(model, messages, tools, num_predict, api_key, max_retries):
    api_key = api_key or os.environ.get("OLLAMA_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    if tools:
        body["tools"] = tools
    delay = 2.0
    for _ in range(max_retries):
        resp = requests.post(OLLAMA_URL, headers=headers, json=body, timeout=180)
        if resp.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _deepseek_chat(model, messages, tools, num_predict, api_key, max_retries):
    """DeepSeek official API (OpenAI-compatible). The response is wrapped
    into a uniform shape but the raw OpenAI tool_calls (with `id`) are
    preserved under `_raw_tool_calls` so run_tool can echo them back
    unchanged in the second turn — required by the OpenAI spec.
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": num_predict,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    delay = 2.0
    for _ in range(max_retries):
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=240)
        if resp.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        if not resp.ok:
            raise requests.HTTPError(
                f"{resp.status_code}: {resp.text[:500]}", response=resp)
        d = resp.json()
        choice = d.get("choices", [{}])[0]
        msg = choice.get("message", {})
        raw_tcs = msg.get("tool_calls") or []
        tcs = []
        for tc in raw_tcs:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tcs.append({"id": tc.get("id"),
                        "function": {"name": fn.get("name", ""),
                                     "arguments": args}})
        return {"message": {
            "role": "assistant",
            "content": msg.get("content") or "",
            "thinking": msg.get("reasoning_content") or "",
            "tool_calls": tcs,
            "_raw_tool_calls": raw_tcs,
        }}
    resp.raise_for_status()
    return {}


def parse_answer(text: str) -> str:
    t = (text or "").strip().upper()
    if "HALLUCINATE" in t:
        return "hallucinated"
    if "REAL" in t:
        return "real"
    return "uncertain"


def lookup_crossref(doi: str) -> dict:
    """Return a compact Crossref record or {'error': ...}."""
    if not doi or "/" not in doi:
        return {"resolved": False, "reason": "no DOI"}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "IntegriRef-bench/1.0"})
        if r.status_code != 200:
            return {"resolved": False, "status": r.status_code}
        m = r.json().get("message", {})
        return {
            "resolved": True,
            "title": (m.get("title") or [""])[0][:200],
            "authors": [a.get("family", "") for a in m.get("author", [])][:5],
            "year": (m.get("issued", {}).get("date-parts", [[None]])[0][0]),
            "venue": (m.get("container-title") or [""])[0][:120],
        }
    except Exception as e:
        return {"resolved": False, "error": str(e)[:120]}


# ─── Per-case run ─────────────────────────────────────────────────────────

def run_parametric(case: dict, model: str,
                    num_predict: int = 1024) -> dict:
    """num_predict default 1024 to give reasoning models (glm, gpt-oss,
    kimi-thinking) room to finish chain-of-thought before emitting the
    final REAL/HALLUCINATED token."""
    authors = case.get("authors", [])
    if isinstance(authors, list):
        authors = ", ".join(authors) if authors else "Unknown"
    prompt = PARAMETRIC_PROMPT.format(
        title=case.get("title", "Unknown"),
        authors=authors,
        year=case.get("year", "Unknown"),
        doi=case.get("doi", "") or "(none)",
        venue=case.get("venue", "") or "(unknown)",
    )
    t0 = time.monotonic()
    try:
        r = ollama_chat(model, [{"role": "user", "content": prompt}],
                        num_predict=num_predict)
        # Some models emit chain-of-thought in "thinking"; concatenate
        # message.content + (a tail of message.thinking) so we can extract REAL/HALLUC
        msg = r.get("message", {})
        text = (msg.get("content") or "") + " " + (msg.get("thinking") or "")[-200:]
        pred = parse_answer(text)
        err = None
    except Exception as e:
        pred, err = "error", str(e)[:200]
    return {
        "id": case.get("id", ""),
        "category": case["category"],
        "ground_truth": case["ground_truth"],
        "prediction": pred,
        "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        "error": err,
    }


def run_tool(case: dict, model: str) -> dict:
    authors = case.get("authors", [])
    if isinstance(authors, list):
        authors = ", ".join(authors) if authors else "Unknown"
    prompt = TOOL_PROMPT.format(
        title=case.get("title", "Unknown"),
        authors=authors,
        year=case.get("year", "Unknown"),
        doi=case.get("doi", "") or "(none)",
        venue=case.get("venue", "") or "(unknown)",
    )
    tools = [{
        "type": "function",
        "function": {
            "name": "lookup_crossref",
            "description": "Look up a paper by DOI on Crossref. Returns the resolved record or {resolved:false}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doi": {"type": "string", "description": "The DOI to look up"}
                },
                "required": ["doi"],
            },
        },
    }]
    messages = [{"role": "user", "content": prompt}]
    t0 = time.monotonic()
    try:
        # Step 1: model decides whether/how to call the tool
        r = ollama_chat(model, messages, tools=tools, num_predict=256)
        msg = r.get("message", {})
        calls = msg.get("tool_calls") or []
        if calls:
            # Execute the first call
            args = calls[0]["function"].get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            doi = args.get("doi") or case.get("doi", "")
            tool_result = lookup_crossref(doi)
            if is_deepseek(model):
                # OpenAI multi-turn requires id + tool_call_id pairing;
                # DeepSeek further requires reasoning_content to be echoed
                # back when the previous response used thinking mode.
                raw_tcs = msg.get("_raw_tool_calls") or calls
                tc_id = (raw_tcs[0].get("id")
                         if raw_tcs and isinstance(raw_tcs[0], dict)
                         else None)
                asst_msg = {"role": "assistant",
                            "content": msg.get("content") or "",
                            "tool_calls": raw_tcs}
                reasoning = msg.get("thinking") or ""
                if reasoning:
                    asst_msg["reasoning_content"] = reasoning
                messages.append(asst_msg)
                messages.append({"role": "tool",
                                 "tool_call_id": tc_id,
                                 "content": json.dumps(tool_result)})
            else:
                messages.append({"role": "assistant",
                                 "content": msg.get("content") or "",
                                 "tool_calls": calls})
                messages.append({"role": "tool",
                                 "content": json.dumps(tool_result)})
            # Step 2: model emits final answer given tool result.
            # Reasoning models need budget to think before emitting REAL/HALLUC.
            r2 = ollama_chat(model, messages, num_predict=512)
            msg2 = r2.get("message", {})
            text = (msg2.get("content") or "") + " " + (msg2.get("thinking") or "")[-200:]
            pred = parse_answer(text)
            tool_used = True
        else:
            # No tool call — fall back to parametric answer
            text = (msg.get("content") or "") + " " + (msg.get("thinking") or "")[-200:]
            pred = parse_answer(text)
            tool_used = False
        err = None
    except Exception as e:
        pred, err, tool_used = "error", str(e)[:200], False
    return {
        "id": case.get("id", ""),
        "category": case["category"],
        "ground_truth": case["ground_truth"],
        "prediction": pred,
        "tool_used": tool_used,
        "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        "error": err,
    }


# ─── Metrics ──────────────────────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict:
    n_h = sum(1 for r in results if r["ground_truth"] == "hallucinated")
    n_r = sum(1 for r in results if r["ground_truth"] == "real")
    tp = sum(1 for r in results
             if r["ground_truth"] == "hallucinated"
             and r["prediction"] == "hallucinated")
    fp = sum(1 for r in results
             if r["ground_truth"] == "real"
             and r["prediction"] == "hallucinated")
    unc = sum(1 for r in results if r["prediction"] == "uncertain")
    err = sum(1 for r in results if r["prediction"] == "error")
    tool_calls = sum(1 for r in results if r.get("tool_used"))
    return {
        "n": len(results),
        "n_halluc": n_h,
        "n_real": n_r,
        "tp": tp,
        "fp": fp,
        "uncertain": unc,
        "errors": err,
        "tool_calls": tool_calls,
        "halluc_recall": f"{tp}/{n_h} = {100*tp/n_h:.1f}%" if n_h else "n/a",
        "fpr":           f"{fp}/{n_r} = {100*fp/n_r:.1f}%" if n_r else "n/a",
    }


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="glm-4.6",
                        help="Ollama model name (e.g. glm-4.6, qwen3-coder:480b, gpt-oss:120b)")
    parser.add_argument("--mode", choices=["parametric", "tool"], default="parametric")
    parser.add_argument("--split", choices=["golden", "rv-sample"], default="golden")
    parser.add_argument("--sample", type=int, default=100,
                        help="Sample size for rv-sample split")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", "-o", type=str, default=None)
    args = parser.parse_args()

    if args.split == "golden":
        cases = load_golden_full()
    else:
        cases = load_rv_sample(args.sample)
    if args.max_cases:
        cases = cases[: args.max_cases]
    logger.info("Loaded %d cases (split=%s, mode=%s, model=%s)",
                len(cases), args.split, args.mode, args.model)

    run_fn = run_tool if args.mode == "tool" else run_parametric

    results = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_fn, c, args.model): c for c in cases}
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(cases):
                logger.info("  [%d/%d] done (%.1fs elapsed)",
                            i, len(cases), time.monotonic() - start)

    metrics = compute_metrics(results)
    print()
    print(f"=== {args.model} / {args.mode} / {args.split} ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    if args.output:
        out = {
            "model": args.model,
            "mode": args.mode,
            "split": args.split,
            "metrics": metrics,
            "results": results,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2))
        logger.info("Wrote %s", args.output)


if __name__ == "__main__":
    main()
