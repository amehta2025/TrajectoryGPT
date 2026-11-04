"""Figure mode (SPEC §9 steps 1-2): figure image and/or methods PDF -> extracted spec -> plan.

Uploads are checked here (PNG/JPEG/PDF only, by content, at most 10 MB each). Images and PDFs go to
Muse Spark natively; if a PDF is rejected, its text is extracted with pypdf and sent instead.
The extracted spec lists each field with its value, confidence and source, and says whether this is an
exact reproduction (the figure's data is one of our datasets) or a method transfer.
"""
from __future__ import annotations

import base64
import io
import hashlib
import json
import uuid
from pathlib import Path

import openai
from pypdf import PdfReader, PdfWriter

from app.agent import prompts
from app.agent.llm import EFFORT, LLM
from app.agent.planner import make_plan
from app.config import CACHE_DIR, load_config

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 2
MAX_PDF_PAGES = 30  # a methods section fits; a 200-page PDF would cost ~146k tokens (red-team F7)
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}
FIGURES_DIR = CACHE_DIR / "figures"  # on the Modal Volume when deployed, so ReproduceView survives restarts
PDF_TEXT_LIMIT = 40_000  # characters of fallback text sent to the model
NOTE_LIMIT = 8_000  # characters of pasted caption/methods text kept with a figure (was 2,000, which cut long captions)


# Figure types figure mode can run, each mapped to tools in the planner prompt (SPEC §9; 2026-10-30).
SUPPORTED_KINDS = {
    "contact_map": "contact maps",
    "difference_contact_map": "difference contact maps",
    "distance_trace": "distance traces",
    "native_contacts": "native contacts",
    "rmsf": "RMSF (flexibility) plots",
}


class UploadRejected(ValueError):
    """str(e) is shown to the user."""


class UnsupportedFigure(ValueError):
    """The figure is not one of SUPPORTED_KINDS. No plan is made (and no planning call is spent)."""

    reason = "unsupported_type"

    def __init__(self, spec: dict, figure: dict, message: str | None = None):
        kinds = ", ".join(SUPPORTED_KINDS.values())
        super().__init__(message or f"This figure type isn't supported yet. TrajectoryGPT can reproduce {kinds}.")
        self.spec, self.figure = spec, figure


class NotAFigure(UnsupportedFigure):
    """The upload is not a scientific figure at all (a photo, a meme, a screenshot of text). No plan is made."""

    reason = "not_a_figure"

    def __init__(self, spec: dict, figure: dict):
        super().__init__(spec, figure, "This doesn't look like a scientific figure.")


def low_confidence(spec: dict) -> list[str]:
    """Names of extracted settings the model marked low confidence; the user must confirm them before a run."""
    return [f["name"] for f in spec.get("fields", []) if f.get("confidence") == "low"]


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
    if mime == "application/pdf":
        raw = _usable_pdf(name, raw)
    return name, mime, raw


def _usable_pdf(name: str, raw: bytes) -> bytes:
    """Refuse password-protected or unreadable PDFs before any model call; keep the first MAX_PDF_PAGES pages."""
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise UploadRejected(f"{name}: this PDF is password-protected. Remove the password and try again.")
        if len(reader.pages) <= MAX_PDF_PAGES:
            return raw
        writer = PdfWriter()
        for page in reader.pages[:MAX_PDF_PAGES]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except UploadRejected:
        raise
    except Exception:  # pypdf raises several error types for damaged files
        raise UploadRejected(f"{name}: this PDF could not be read. Try exporting it again.") from None


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


COLORMAPS = ["viridis", "magma", "greys", "blues", "reds", "red_blue", "other"]


def display_schema() -> dict:
    """How the published figure is drawn (axes, residue ranges, colour scale, reference line), so ours can be
    drawn the same way for a side-by-side look. Display only: it never changes what is computed."""
    num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    axis = {"type": "object", "properties": {
        "title": {"type": "string"},
        "group": {"type": "string", "enum": ["LID", "NMP", "CORE", "none"]},
        "residue_min": num, "residue_max": num},
        "required": ["title", "group", "residue_min", "residue_max"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "x": axis, "y": axis, "color_min": num, "color_max": num,
        "colormap": {"type": "string", "enum": COLORMAPS},
        "time_unit": {"type": "string", "enum": ["ns", "us", "ps", "frame", "none"]},
        "reference_line": num, "y_min": num, "y_max": num},
        "required": ["x", "y", "color_min", "color_max", "colormap", "time_unit", "reference_line", "y_min", "y_max"],
        "additionalProperties": False}


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
            "is_figure": {"type": "boolean",
                          "description": "True only for a scientific figure or plot (axes, data, a structure image from a paper). "
                                         "False for photos of everyday things, memes, selfies, or screenshots of plain text."},
            "kind": {"type": "string", "enum": sorted(SUPPORTED_KINDS) + ["other"],
                     "description": "The figure type; 'other' for anything not in the list. Never force-fit."},
            "mode": {"type": "string", "enum": ["exact", "transfer"]},
            "dataset": {"type": "string", "enum": datasets + ["none"],
                        "description": "Our dataset the figure was made from, or none."},
            "closest_dataset": {"type": "string", "enum": datasets + ["none"],
                                "description": "For a method transfer: which of our datasets matches the kind of data "
                                               "the paper used (equilibrium MD or a transition path), or none if the "
                                               "paper does not say."},
            "mode_reason": {"type": "string", "description": "Why exact or transfer; cite the evidence."},
            "fields": {"type": "array", "items": field},
            "protein_is_adk": {"type": "boolean", "description": "True only if the figure's protein is adenylate kinase."},
            "named_residues": {"type": "array", "items": {"type": "string"},
                               "description": "Residues the analysis is about, as printed (e.g. Asp45, Arg112); [] if none."},
            "display": display_schema(),
            "question": {"type": "string", "description": "The analysis to run, as a plain request for the planner."},
        },
        "required": ["summary", "is_figure", "kind", "mode", "dataset", "closest_dataset", "mode_reason", "fields", "protein_is_adk",
                     "named_residues", "display", "question"],
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
        spec["foreign_residues"] = foreign_residues(spec)
        return spec, usages
    raise RuntimeError("unreachable")


PAIR_KINDS = {"distance_trace"}  # figure types about one named residue pair


def foreign_residues(spec: dict) -> list[str]:
    """Residues a figure names from another protein: their numbers mean nothing on AdK, so the user picks the
    AdK pair before anything runs (start_run refuses until then). Decided here in code, not by the model."""
    named = [str(r) for r in spec.get("named_residues") or []][:4]
    if spec.get("kind") in PAIR_KINDS and named and spec.get("protein_is_adk") is False:
        return named
    return []


MAX_FIGURE_PIXELS = 40_000_000  # an embedded image larger than this is skipped (decompression-bomb guard)


def figure_from_pdf(raw: bytes) -> bytes | None:
    """The figure embedded in a methods PDF (its largest raster image, at least 300 x 200 px) as PNG on white,
    so ReproduceView can show the paper's figure beside ours; None if the PDF embeds no such image."""
    from PIL import Image
    try:
        best = None
        for page in PdfReader(io.BytesIO(raw)).pages[:MAX_PDF_PAGES]:
            for im in page.images:
                w, h = im.image.size
                if w >= 300 and h >= 200 and w * h <= MAX_FIGURE_PIXELS and (best is None or w * h > best[0]):
                    best = (w * h, im.image)
        if best is None:
            return None
        img = best[1]
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            white = Image.new("RGBA", img.size, "white")
            img = Image.alpha_composite(white, img)
        out = io.BytesIO()
        img.convert("RGB").save(out, "PNG")
        return out.getvalue()
    except Exception:  # an unreadable image just means no extracted figure
        return None


def save_figure(files: list[tuple[str, str, bytes]], directory: Path | None = None) -> dict:
    """Store the first image for ReproduceView, else the figure embedded in the PDF (else the PDF itself);
    returns {figure_id, filename, mime, sha256}. sha256 is of the uploaded file."""
    d = directory or FIGURES_DIR
    d.mkdir(parents=True, exist_ok=True)
    name, mime, raw = next((f for f in files if f[1].startswith("image/")), files[0])
    sha = hashlib.sha256(raw).hexdigest()
    if mime == "application/pdf" and (png := figure_from_pdf(raw)):
        name, mime, raw = f"{name} (figure)", "image/png", png
    fid = uuid.uuid4().hex[:16]
    (d / fid).write_bytes(raw)
    (d / f"{fid}.json").write_text(json.dumps({"filename": name, "mime": mime}), encoding="utf-8")
    return {"figure_id": fid, "filename": name, "mime": mime, "sha256": sha}


def load_figure(figure_id: str, directory: Path | None = None) -> tuple[bytes, str]:
    if not figure_id.isalnum():
        raise KeyError(figure_id)
    d = directory or FIGURES_DIR
    try:
        meta = json.loads((d / f"{figure_id}.json").read_text(encoding="utf-8"))
        return (d / figure_id).read_bytes(), meta["mime"]
    except FileNotFoundError:
        raise KeyError(figure_id) from None


def read_figure(llm: LLM, raw_files: list[dict], note: str = "") -> tuple[dict, list]:
    """Validate uploads, extract the spec and store the figure: (figure with its spec, usages). The UI shows
    what was read while the plan is drafted (two requests, so the reading isn't hidden behind the plan)."""
    if not 1 <= len(raw_files) <= MAX_FILES:
        raise UploadRejected(f"attach 1-{MAX_FILES} files (a figure image and/or a methods PDF)")
    files = [decode_upload(f) for f in raw_files]
    spec, usages = extract_spec(llm, files, note)
    figure = {**save_figure(files), "spec": spec}
    if not spec.get("is_figure", True):
        raise NotAFigure(spec, figure)
    if spec["kind"] not in SUPPORTED_KINDS:
        raise UnsupportedFigure(spec, figure)
    return figure, usages


def transfer_dataset(spec: dict, selected_dataset: str) -> str:
    """Where a method transfer runs: the dataset matching the kind of data the paper used (its methods: equilibrium
    MD or a transition path), else the picker's. The picker alone was wrong when a sample had left it on the
    transition path and the paper described 1 us of equilibrium MD (live run 20261104-014851). The user can still
    change it on the plan card."""
    closest = spec.get("closest_dataset")
    return closest if closest in load_config()["datasets"] else selected_dataset


MAP_KINDS = {"contact_map", "difference_contact_map"}


def figure_ranges(spec: dict, plan: dict) -> dict[str, list[int]] | None:
    """The residue ranges a contact map's axes span, for the plan's groups (set by code from spec.display, not by
    the planner). They limit what is tested, so the statistics cover the pairs the paper showed; the user can
    change them on the plan card."""
    if spec.get("kind") not in MAP_KINDS:
        return None
    out = {}
    for ax in ((spec.get("display") or {}).get("x"), (spec.get("display") or {}).get("y")):
        if not ax or ax.get("group") not in (plan.get("group_a"), plan.get("group_b")):
            continue
        lo, hi = ax.get("residue_min"), ax.get("residue_max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo <= hi:
            out[ax["group"]] = [int(round(lo)), int(round(hi))]
    return out or None


def plan_from_spec(llm: LLM, figure: dict, selected_dataset: str) -> dict:
    """The analysis plan for a figure already read. Returns what /figure/plan sends."""
    spec = figure["spec"]
    dataset = transfer_dataset(spec, selected_dataset) if spec["mode"] != "exact" else spec["dataset"]
    fields = "; ".join(f"{f['name']}: {f['value']} ({f['confidence']}, {f['source']})" for f in spec["fields"])
    question = (f"{spec['question']}\n\nThis comes from an uploaded figure ({spec['kind']}, "
                f"{'exact reproduction on ' + dataset if spec['mode'] == 'exact' else 'method transfer'}). "
                f"Extracted spec: {fields}")
    foreign = spec.get("foreign_residues") or []
    if foreign:
        question += (f"\n\nThe residues {', '.join(foreign)} belong to another protein; their numbers mean nothing on AdK. "
                     "The user picks the AdK residue pair before the run; plan the tools without naming a pair.")
    plan, usages = make_plan(llm, question, dataset, effort=EFFORT["figure_plan"])
    plan_ranges = figure_ranges(spec, plan)
    if plan_ranges:
        plan["residue_ranges"] = plan_ranges
    if foreign:  # "Residue 45 is LEU, not ASP; the analysis uses LEU45" would contradict the user's pick
        plan["notes"] = [n for n in plan.get("notes", []) if not n.startswith(("Residue ", "There is no residue"))]
    return {"plan": plan, "figure": figure, "question": question, "usages": usages}


def plan_from_figure(llm: LLM, raw_files: list[dict], selected_dataset: str, note: str = "") -> dict:
    """Read and plan in one go (the CLI and the one-request /figure/plan)."""
    figure, usages = read_figure(llm, raw_files, note)
    out = plan_from_spec(llm, figure, selected_dataset)
    return {**out, "usages": usages + out["usages"]}
