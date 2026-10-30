"""Figure mode (SPEC §9 steps 1-2): figure image and/or methods PDF -> extracted spec -> plan.

Uploads are checked here (PNG/JPEG/PDF only, by content, at most 10 MB each). Images and PDFs go to
Muse Spark natively; if a PDF is rejected, its text is extracted with pypdf and sent instead.
The extracted spec lists each field with its value, confidence and source, and says whether this is an
exact reproduction (the figure's data is one of our datasets) or a method transfer.
"""
from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path

import openai
from pypdf import PdfReader

from app.agent import prompts
from app.agent.llm import EFFORT, LLM
from app.agent.planner import make_plan
from app.config import CACHE_DIR, load_config

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 2
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}
FIGURES_DIR = CACHE_DIR / "figures"  # on the Modal Volume when deployed, so ReproduceView survives restarts
PDF_TEXT_LIMIT = 40_000  # characters of fallback text sent to the model


class UploadRejected(ValueError):
    """str(e) is shown to the user."""


def decode_upload(f: dict) -> tuple[str, str, bytes]:
    """{filename, mime, data (base64 or data URL)} -> (filename, mime, bytes); rejects bad type or size."""
    name, mime, data = str(f.get("filename") or "upload"), f.get("mime"), f.get("data") or ""
    if mime not in MAGIC:
        raise UploadRejected(f"{name}: only PNG, JPEG or PDF files are accepted")
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    if len(data) > (MAX_BYTES * 4) // 3 + 4:
        raise UploadRejected(f"{name}: larger than 10 MB")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise UploadRejected(f"{name}: not valid base64") from None
    if len(raw) > MAX_BYTES:
        raise UploadRejected(f"{name}: larger than 10 MB")
    if not raw.startswith(MAGIC[mime]):
        raise UploadRejected(f"{name}: the content is not a {mime.split('/')[1].upper()} file")
    return name, mime, raw


def pdf_text(raw: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:30])
    return text[:PDF_TEXT_LIMIT]


def _content(files: list[tuple[str, str, bytes]], pdf_as_text: bool) -> list[dict]:
    parts: list[dict] = []
    for name, mime, raw in files:
        url = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        if mime.startswith("image/"):
            parts.append({"type": "input_image", "image_url": url})
        elif pdf_as_text:
            parts.append({"type": "input_text", "text": f"Text of {name} (extracted with pypdf):\n{pdf_text(raw)}"})
        else:
            parts.append({"type": "input_file", "filename": name, "file_data": url})
    return parts


def spec_schema() -> dict:
    datasets = sorted(load_config()["datasets"])
    field = {"type": "object", "properties": {
        "name": {"type": "string"},
        "value": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "source": {"type": "string", "enum": ["caption", "methods", "axis", "assumed"]}},
        "required": ["name", "value", "confidence", "source"], "additionalProperties": False}
    return {"name": "FigureSpec", "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what the figure shows."},
            "kind": {"type": "string", "enum": ["native_contacts", "contact_map", "distance_trace", "other"]},
            "mode": {"type": "string", "enum": ["exact", "transfer"]},
            "dataset": {"type": "string", "enum": datasets + ["none"],
                        "description": "Our dataset the figure was made from, or none."},
            "mode_reason": {"type": "string", "description": "Why exact or transfer; cite the evidence."},
            "fields": {"type": "array", "items": field},
            "question": {"type": "string", "description": "The analysis to run, as a plain request for the planner."},
        },
        "required": ["summary", "kind", "mode", "dataset", "mode_reason", "fields", "question"],
        "additionalProperties": False,
    }}


def extract_spec(llm: LLM, files: list[tuple[str, str, bytes]], note: str = "") -> tuple[dict, list]:
    """One Muse Spark call (medium effort) -> (spec, usages). Falls back to PDF text if a PDF is rejected."""
    usages = []
    for pdf_as_text in (False, True):
        user = [{"type": "input_text", "text": prompts.figure_user(note)}] + _content(files, pdf_as_text)
        try:
            r = llm.run_turn([{"role": "system", "content": prompts.FIGURE_SYSTEM}, {"role": "user", "content": user}],
                             schema=spec_schema(), effort=EFFORT["extract"])
        except openai.BadRequestError:
            if pdf_as_text or not any(m == "application/pdf" for _, m, _ in files):
                raise
            continue
        usages.append(r.usage)
        spec = r.parsed
        if spec["mode"] == "exact" and spec["dataset"] == "none":
            spec["mode"] = "transfer"  # exact needs one of our datasets
        spec["pdf_fallback"] = pdf_as_text
        return spec, usages
    raise RuntimeError("unreachable")


def save_figure(files: list[tuple[str, str, bytes]], directory: Path | None = None) -> dict:
    """Store the first image (or PDF) for ReproduceView; returns {figure_id, filename, mime}."""
    d = directory or FIGURES_DIR
    d.mkdir(parents=True, exist_ok=True)
    name, mime, raw = next((f for f in files if f[1].startswith("image/")), files[0])
    fid = uuid.uuid4().hex[:16]
    (d / fid).write_bytes(raw)
    (d / f"{fid}.json").write_text(json.dumps({"filename": name, "mime": mime}), encoding="utf-8")
    return {"figure_id": fid, "filename": name, "mime": mime}


def load_figure(figure_id: str, directory: Path | None = None) -> tuple[bytes, str]:
    if not figure_id.isalnum():
        raise KeyError(figure_id)
    d = directory or FIGURES_DIR
    try:
        meta = json.loads((d / f"{figure_id}.json").read_text(encoding="utf-8"))
        return (d / figure_id).read_bytes(), meta["mime"]
    except FileNotFoundError:
        raise KeyError(figure_id) from None


def plan_from_figure(llm: LLM, raw_files: list[dict], selected_dataset: str, note: str = "") -> dict:
    """Validate uploads, extract the spec, store the figure, and plan. Returns what /figure/plan sends."""
    if not 1 <= len(raw_files) <= MAX_FILES:
        raise UploadRejected(f"attach 1-{MAX_FILES} files (a figure image and/or a methods PDF)")
    files = [decode_upload(f) for f in raw_files]
    spec, usages = extract_spec(llm, files, note)
    dataset = spec["dataset"] if spec["mode"] == "exact" else selected_dataset
    fields = "; ".join(f"{f['name']}: {f['value']} ({f['confidence']}, {f['source']})" for f in spec["fields"])
    question = (f"{spec['question']}\n\nThis comes from an uploaded figure ({spec['kind']}, "
                f"{'exact reproduction on ' + dataset if spec['mode'] == 'exact' else 'method transfer'}). "
                f"Extracted spec: {fields}")
    plan, plan_usages = make_plan(llm, question, dataset)
    figure = {**save_figure(files), "spec": spec}
    return {"plan": plan, "figure": figure, "question": question, "usages": usages + plan_usages}
