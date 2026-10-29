"""Live smoke test of the LLM wrapper (SPEC §6b, Day 4 morning): tool call + replay, structured output, image.

Run from backend/:  python -m scripts.smoke_llm [meta|baseten|both] [--model MODEL]
Uses META_MODEL / BASETEN_MODEL from .env; aborts if the estimated cost passes $0.50.
"""
import argparse
import base64
import struct
import sys
import zlib

from app.agent.llm import EFFORT, LLM, CostTracker, tool_result_item

TOOL = {
    "type": "function",
    "name": "get_residue_name",
    "description": "Return the three-letter residue name of a residue in adenylate kinase.",
    "parameters": {
        "type": "object",
        "properties": {"resid": {"type": "integer", "description": "Residue number"}},
        "required": ["resid"],
        "additionalProperties": False,
    },
}
SCHEMA = {
    "name": "Answer",
    "schema": {
        "type": "object",
        "properties": {"domain": {"type": "string", "enum": ["LID", "NMP", "CORE"]}},
        "required": ["domain"],
        "additionalProperties": False,
    },
}


def two_colour_png(w: int = 512, h: int = 256) -> str:
    """Left half red, right half blue, as a base64 PNG data URL (no image library needed)."""
    row = b"\x00" + bytes([255, 0, 0]) * (w // 2) + bytes([0, 0, 255]) * (w // 2)
    raw = row * h

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def show(label: str, r) -> None:
    u = r.usage
    cost = "n/a" if u.cost_usd is None else f"${u.cost_usd:.5f}"
    print(f"  {label}: {u.latency_s:.1f}s effort={u.effort} in={u.input_tokens} cached={u.cached_tokens} "
          f"out={u.output_tokens} (reasoning {u.reasoning_tokens}) cost={cost}")


def smoke(provider: str, model: str | None) -> bool:
    tracker = CostTracker(limit_usd=0.50)
    llm = LLM(provider, model=model, tracker=tracker)
    print(f"[{provider}] model={llm.model}")
    ok = True

    # 1. Tool call, then replay the output items (incl. encrypted reasoning) with the tool result.
    history = [{"role": "user", "content": "What is the residue name of residue 134? Use the tool, then answer in one word."}]
    r = llm.run_turn(history, [TOOL], effort=EFFORT["loop"])
    show("tool call", r)
    if not r.tool_calls:
        print("  FAIL: no tool call;", r.final_text[:200]); return False
    print(f"  called {r.tool_calls[0].name}({r.tool_calls[0].arguments})  items={[i.get('type') for i in r.output_items]}")
    history += r.output_items + [tool_result_item(r.tool_calls[0].call_id, '{"resname": "HSE"}')]
    r = llm.run_turn(history, [TOOL], effort=EFFORT["loop"])
    show("after result", r)
    print(f"  final: {r.final_text.strip()[:80]!r}")
    ok &= "HSE" in r.final_text.upper()

    # 2. Structured output.
    r = llm.run_turn([{"role": "user", "content": "In E. coli adenylate kinase, which domain contains residues 122-159?"}],
                     schema=SCHEMA, effort=EFFORT["plan"])
    show("structured", r)
    print(f"  parsed: {r.parsed}")
    ok &= r.parsed == {"domain": "LID"}

    # 3. Image input.
    img = [{"role": "user", "content": [
        {"type": "input_text", "text": "This image has two colour halves. Name the left colour, then the right colour, in two words."},
        {"type": "input_image", "image_url": two_colour_png()},
    ]}]
    r = llm.run_turn(img, effort=EFFORT["loop"])
    show("image", r)
    print(f"  final: {r.final_text.strip()[:80]!r}")
    ok &= "red" in r.final_text.lower() and "blue" in r.final_text.lower()

    total = f"${tracker.total_usd:.5f}" if any(u.cost_usd is not None for u in tracker.calls) else "n/a"
    print(f"  {'PASS' if ok else 'FAIL'}  calls={len(tracker.calls)} total cost={total}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", nargs="?", default="both", choices=["meta", "baseten", "both"])
    ap.add_argument("--model")
    a = ap.parse_args()
    providers = ["meta", "baseten"] if a.provider == "both" else [a.provider]
    results = {p: smoke(p, a.model) for p in providers}
    sys.exit(0 if all(results.values()) else 1)
