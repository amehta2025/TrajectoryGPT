"""Figure mode without the network: upload checks, spec extraction (and the PDF text fallback), planning,
the reproduction_check tool, and the /figure/plan gate."""
import base64
import json

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

import app.agent.figure as figure
from app.agent.llm import TurnResult, Usage
from app.agent.tools import ApprovedPlan, RunState, run_tool
from app.config import REPO_ROOT
from app.main import app

SAMPLE = REPO_ROOT / "assets" / "figures" / "userguide_q1q2_replot.png"
PNG = SAMPLE.read_bytes()
PDF = b"%PDF-1.4\n%fake\n"

SPEC = {"summary": "Q1 and Q2 along the AdK transition path", "is_figure": True, "kind": "native_contacts", "mode": "exact",
        "dataset": "adk_transition", "mode_reason": "caption cites the MDAnalysis User Guide Q1 vs Q2 example",
        "fields": [{"name": "atom selection", "value": "name CA", "confidence": "high", "source": "caption"},
                   {"name": "radius", "value": "8 Å", "confidence": "medium", "source": "assumed"}],
        "question": "Compute Q1 and Q2 along the transition path and check them against the published code."}
PLAN = {"summary": "Reproduce Q1/Q2", "dataset": "adk_transition", "group_a": "LID", "group_b": "CORE",
        "cutoff": 4.5, "window_a": [0, 98], "window_b": None, "stride": 1, "sequence_exclusion": 2,
        "robustness_cutoffs": [4.0, 4.5, 5.0],
        "steps": [{"tool": "native_contacts", "rationale": ""}, {"tool": "reproduction_check", "rationale": ""},
                  {"tool": "submit_report", "rationale": ""}]}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


class SchemaLLM:
    """Returns the scripted parsed objects in order; raises an exception if one is scripted."""

    model = "fake"
    provider = "fake"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def run_turn(self, input_items, tools=None, *, schema=None, effort):
        self.calls.append(input_items)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        u = Usage("meta", "fake", effort, cost_usd=0.001)
        return TurnResult([], json.dumps(nxt), nxt, [{"type": "message", "role": "assistant",
                                                      "content": [{"type": "output_text", "text": "{}"}]}], u)


@pytest.fixture(autouse=True)
def tmp_figures(tmp_path, monkeypatch):
    monkeypatch.setattr(figure, "FIGURES_DIR", tmp_path)
    return tmp_path


# ---------- uploads ----------

def test_accepts_png_and_data_urls():
    name, mime, raw = figure.decode_upload({"filename": "f.png", "mime": "image/png", "data": "data:image/png;base64," + b64(PNG)})
    assert (name, mime, raw) == ("f.png", "image/png", PNG)


@pytest.mark.parametrize("f,msg", [
    ({"filename": "a.gif", "mime": "image/gif", "data": b64(b"GIF89a")}, "only PNG, JPEG or PDF"),
    ({"filename": "a.png", "mime": "image/png", "data": b64(PDF)}, "not a PNG"),
    ({"filename": "a.pdf", "mime": "application/pdf", "data": b64(PNG)}, "not a PDF"),
    ({"filename": "a.png", "mime": "image/png", "data": "!!!"}, "base64"),
])
def test_rejects_bad_uploads(f, msg):
    with pytest.raises(figure.UploadRejected, match=msg):
        figure.decode_upload(f)


def test_rejects_over_10_mb():
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (figure.MAX_BYTES + 1)
    with pytest.raises(figure.UploadRejected, match="10 MB"):
        figure.decode_upload({"filename": "big.png", "mime": "image/png", "data": b64(big)})


# ---------- extraction, PDF fallback, planning ----------

def test_image_goes_to_the_model_natively():
    llm = SchemaLLM([SPEC])
    spec, _ = figure.extract_spec(llm, [("f.png", "image/png", PNG)])
    assert spec["mode"] == "exact" and not spec["pdf_fallback"]
    parts = llm.calls[0][1]["content"]
    assert parts[1]["type"] == "input_image" and parts[1]["image_url"].startswith("data:image/png;base64,")


def test_rejected_pdf_falls_back_to_text(monkeypatch):
    monkeypatch.setattr(figure, "pdf_text", lambda raw: "Methods: CA atoms, 8 A radius.")
    resp = httpx.Response(400, request=httpx.Request("POST", "https://x/v1/responses"))
    llm = SchemaLLM([openai.BadRequestError("file rejected", response=resp, body=None), SPEC])
    spec, _ = figure.extract_spec(llm, [("f.png", "image/png", PNG), ("m.pdf", "application/pdf", PDF)])
    assert spec["pdf_fallback"]
    assert llm.calls[0][1]["content"][2]["type"] == "input_file"
    assert "Methods: CA atoms" in llm.calls[1][1]["content"][2]["text"]


def test_exact_without_one_of_our_datasets_becomes_transfer():
    spec, _ = figure.extract_spec(SchemaLLM([{**SPEC, "dataset": "none"}]), [("f.png", "image/png", PNG)])
    assert spec["mode"] == "transfer"


def test_plan_from_figure_stores_the_figure():
    out = figure.plan_from_figure(SchemaLLM([SPEC, PLAN]),
                                  [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}], "adk_equilibrium")
    assert out["plan"]["dataset"] == "adk_transition"  # exact mode uses the figure's dataset
    assert "Extracted spec: atom selection: name CA (high, caption)" in out["question"]
    raw, mime = figure.load_figure(out["figure"]["figure_id"])
    assert raw == PNG and mime == "image/png" and out["figure"]["spec"]["kind"] == "native_contacts"


# ---------- reproduction_check tool ----------

def test_reproduction_check_tool_matches():
    s = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98], steps=PLAN["steps"]))
    r = run_tool(s, "reproduction_check", "{}")
    assert r["status"] == "ok" and r["values"]["match"]["v"] is True
    assert r["values"]["max_abs_diff"]["v"] <= r["values"]["tolerance"]["v"]
    assert run_tool(s, "reproduction_check", '{"selection": "LID CORE"}')["status"] == "refused"


# ---------- endpoint gate ----------

def test_figure_plan_is_gated_and_checks_type(monkeypatch):
    monkeypatch.setenv("DEMO_PASSCODE", "s3cret")
    c = TestClient(app)
    body = {"files": [{"filename": "a.gif", "mime": "image/gif", "data": b64(b"GIF89a")}]}
    assert c.post("/figure/plan", json=body).status_code == 401
    r = c.post("/figure/plan", json=body, headers={"X-Demo-Passcode": "s3cret"})
    assert r.status_code == 400 and "only PNG, JPEG or PDF" in r.json()["detail"]
    assert c.get("/figures/nope").status_code == 404


# ---------- supported figure types and low-confidence confirmations ----------

def test_schema_lists_the_five_supported_types_plus_other():
    kinds = figure.spec_schema()["schema"]["properties"]["kind"]["enum"]
    assert set(kinds) == {"contact_map", "difference_contact_map", "distance_trace", "native_contacts", "rmsf", "other"}


def test_unsupported_figure_stops_before_planning():
    llm = SchemaLLM([{**SPEC, "kind": "other", "summary": "RMSD versus time"}])  # no plan scripted: planning must not run
    with pytest.raises(figure.UnsupportedFigure) as e:
        figure.plan_from_figure(llm, [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}], "adk_equilibrium")
    assert "isn't supported yet" in str(e.value) and "RMSF" in str(e.value)
    assert len(llm.calls) == 1 and e.value.figure["figure_id"]


def test_unsupported_figure_endpoint_returns_a_clear_message(monkeypatch):
    import app.agent.session as session
    monkeypatch.setattr(session, "LLM", lambda *a, **kw: SchemaLLM([{**SPEC, "kind": "other"}]))
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    r = TestClient(app).post("/figure/plan", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}]})
    assert r.status_code == 422 and "isn't supported yet" in r.json()["detail"]["unsupported"]


def test_low_confidence_fields_must_be_confirmed_before_a_run(monkeypatch):
    import app.agent.session as session
    from app.agent.planner import PlanInvalid
    spec = {**SPEC, "fields": SPEC["fields"] + [{"name": "reference frames", "value": "first and last", "confidence": "low", "source": "assumed"}]}
    monkeypatch.setattr(session, "LLM", lambda *a, **kw: SchemaLLM([spec, PLAN]))
    started = []
    monkeypatch.setattr(session, "RunHandle", lambda run, step_through=False: started.append(run) or type("H", (), {"start": lambda self: None})())
    out = session.propose_figure_plan([{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}], "adk_transition")
    assert figure.low_confidence(out["figure"]["spec"]) == ["reference frames"]
    with pytest.raises(PlanInvalid) as e:
        session.start_run(out["plan_id"], {}, confirmed_fields=[])
    assert "reference frames" in str(e.value) and not started


def test_long_caption_text_reaches_extraction_intact(monkeypatch):
    """A long pasted caption plus methods (the mdciao case was 2,170 characters) is not cut at 2,000."""
    import app.agent.session as session
    seen = {}
    monkeypatch.setattr(session, "propose_figure_plan", lambda files, dataset, note, **kw: seen.setdefault("note", note) and {})
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    note = "Caption. " * 600 + "Methods: closest heavy atoms, 4.5 Å."
    TestClient(app).post("/figure/plan", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}], "note": note})
    assert len(note) > 5000 and seen["note"] == note
    assert figure.NOTE_LIMIT >= 8000


def test_a_photo_that_is_not_a_figure_gets_its_own_message():
    llm = SchemaLLM([{**SPEC, "is_figure": False, "kind": "other", "summary": "A plate of pasta."}])
    with pytest.raises(figure.NotAFigure) as e:
        figure.plan_from_figure(llm, [{"filename": "food.png", "mime": "image/png", "data": b64(PNG)}], "adk_equilibrium")
    assert str(e.value) == "This doesn't look like a scientific figure." and e.value.reason == "not_a_figure"
    assert len(llm.calls) == 1  # no planning call


def test_not_a_figure_endpoint_says_so(monkeypatch):
    import app.agent.session as session
    monkeypatch.setattr(session, "LLM", lambda *a, **kw: SchemaLLM([{**SPEC, "is_figure": False, "kind": "other"}]))
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    r = TestClient(app).post("/figure/plan", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}]})
    d = r.json()["detail"]
    assert r.status_code == 422 and d["reason"] == "not_a_figure" and "scientific figure" in d["unsupported"]


def test_schema_asks_whether_it_is_a_figure_and_counts_bar_charts_as_contact_maps():
    from app.agent import prompts
    assert "is_figure" in figure.spec_schema()["schema"]["required"]
    assert "bar chart" in prompts.FIGURE_SYSTEM


# ---------- PDFs: password-protected or damaged files are refused; long ones are trimmed (red-team C6, F7) ----------

def _pdf(pages=1, password=None) -> bytes:
    import io
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if password:
        w.encrypt(password)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _upload(raw: bytes) -> dict:
    import base64
    return {"filename": "methods.pdf", "mime": "application/pdf", "data": base64.b64encode(raw).decode()}


def test_password_protected_pdf_is_refused_before_any_model_call():
    with pytest.raises(figure.UploadRejected, match="password-protected"):
        figure.decode_upload(_upload(_pdf(password="secret")))


def test_damaged_pdf_is_refused():
    with pytest.raises(figure.UploadRejected, match="could not be read"):
        figure.decode_upload(_upload(b"%PDF-1.7 this is not really a pdf"))


def test_long_pdf_keeps_only_the_first_pages():
    import io
    from pypdf import PdfReader
    _, _, raw = figure.decode_upload(_upload(_pdf(pages=45)))
    assert len(PdfReader(io.BytesIO(raw)).pages) == figure.MAX_PDF_PAGES
    _, _, short = figure.decode_upload(_upload(_pdf(pages=3)))
    assert len(PdfReader(io.BytesIO(short)).pages) == 3


# ---------- two requests: read the figure, then plan from what was read (UI shows the reading early) ----------

def test_read_then_plan_over_http(monkeypatch):
    import app.agent.session as session
    monkeypatch.setattr(session, "LLM", lambda *a, **kw: SchemaLLM([SPEC, PLAN]))
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    c = TestClient(app)
    read = c.post("/figure/read", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}]}).json()
    assert read["figure"]["spec"]["kind"] == "native_contacts" and "plan" not in read
    r = c.post(f"/figure/read/{read['read_id']}/plan", json={"dataset": "adk_equilibrium"}).json()
    assert r["plan"]["dataset"] == "adk_transition" and r["figure"]["figure_id"] == read["figure"]["figure_id"]
    assert len(r["usage"]) == 2  # the reading call and the planning call
    assert c.post(f"/figure/read/{read['read_id']}/plan", json={}).status_code == 404  # a read is planned once


def test_read_of_an_unsupported_figure_says_so(monkeypatch):
    import app.agent.session as session
    monkeypatch.setattr(session, "LLM", lambda *a, **kw: SchemaLLM([{**SPEC, "kind": "other"}]))
    monkeypatch.delenv("DEMO_PASSCODE", raising=False)
    r = TestClient(app).post("/figure/read", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG)}]})
    assert r.status_code == 422 and "isn't supported yet" in r.json()["detail"]["unsupported"]


def test_read_in_replay_mode_returns_the_recorded_plan_or_a_clear_miss(monkeypatch):
    from app.safety import replay
    monkeypatch.setenv("APP_MODE", "replay")
    c = TestClient(app)
    raw = (REPO_ROOT / "frontend" / "public" / "figures" / "userguide_q1q2_replot.png").read_bytes()
    r = c.post("/figure/read", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(raw)}]})
    if replay.match_figure([("f.png", "image/png", raw)]) is not None:
        assert r.status_code == 200 and r.json()["proposal"]["plan_id"].startswith("replay:")
    miss = c.post("/figure/read", json={"files": [{"filename": "f.png", "mime": "image/png", "data": b64(PNG + b"other")}]})
    assert miss.status_code == 409 and miss.json()["detail"]["replay"] is True


def test_native_settings_are_validated_plan_parameters():
    from app.agent.planner import to_approved, validate_plan
    ok = {**PLAN, "native_radius": 6.0, "native_selection": "heavy atoms", "ref_frames": [0]}
    a = to_approved(ok)
    assert (a.native_radius, a.native_selection, a.ref_frames) == (6.0, "heavy atoms", [0])
    assert to_approved(PLAN).native_radius == 8.0  # a plan made before these settings existed
    errs = validate_plan({**PLAN, "native_radius": 40, "native_selection": "LID CORE", "ref_frames": [500]})
    assert len([e for e in errs if "native_" in e or "ref_frames" in e]) == 3


def test_spec_schema_has_a_display_block_strict_mode_accepts():
    s = figure.spec_schema()["schema"]
    d = s["properties"]["display"]
    assert "display" in s["required"] and set(d["required"]) == set(d["properties"]) and d["additionalProperties"] is False
    assert d["properties"]["x"]["properties"]["residue_min"] == {"anyOf": [{"type": "number"}, {"type": "null"}]}


def test_reproduction_check_is_refused_off_the_published_data():
    """The published Q1/Q2 figure was made from adk_transition; on another trajectory there is nothing to
    reproduce (the agent once added the check to a method transfer on adk_equilibrium)."""
    s = RunState(ApprovedPlan("adk_equilibrium", "LID", "CORE", 4.5, [0, 4187], steps=PLAN["steps"]))
    r = run_tool(s, "reproduction_check", "{}")
    assert r["status"] == "refused" and "adk_transition" in r["summary_for_model"]


# ---------- residues named from another protein (method transfer) ----------

def test_foreign_residues_are_flagged_by_code_only_for_another_protein():
    spec = {"kind": "distance_trace", "protein_is_adk": False, "named_residues": ["Asp45", "Arg112"]}
    assert figure.foreign_residues(spec) == ["Asp45", "Arg112"]
    assert figure.foreign_residues({**spec, "protein_is_adk": True}) == []  # AdK numbers are AdK numbers
    assert figure.foreign_residues({**spec, "named_residues": []}) == []
    assert figure.foreign_residues({**spec, "kind": "rmsf"}) == []


def test_a_chosen_pair_is_validated_and_used_exactly():
    from app.agent.planner import to_approved, validate_plan
    from app.agent.tools import resolve_pair
    assert any("residue ASP45 is not in this protein" in e for e in validate_plan({**PLAN, "pair": "ASP45-ARG112"}))
    assert any("look like" in e for e in validate_plan({**PLAN, "pair": "Asp45 to Arg112"}))
    a = to_approved({**PLAN, "pair": "asp54-lys157"})
    assert a.pair == "ASP54-LYS157"
    ka, kb = resolve_pair(a, None)  # the agent may omit it
    assert ka.endswith(":54:ASP") and kb.endswith(":157:LYS")
    s = RunState(a)
    assert run_tool(s, "distance_over_time", '{"pair": "LEU45-PRO112"}')["status"] == "refused"
    assert run_tool(s, "distance_over_time", "{}")["status"] == "ok"


def test_run_is_blocked_until_the_user_picks_the_adk_pair(monkeypatch, tmp_path):
    from app.agent import session
    from app.agent.planner import PlanInvalid
    monkeypatch.setattr(session, "PLANS_DIR", tmp_path)
    plan = {**PLAN, "steps": [{"tool": "distance_over_time", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]}
    spec = {"kind": "distance_trace", "fields": [], "foreign_residues": ["Asp45", "Arg112"]}
    monkeypatch.setitem(session.PLANS, "pf1", {"question": "q", "dataset": "adk_transition", "plan": plan, "usage": [],
                                               "llm": None, "figure": {"spec": spec}})
    with pytest.raises(PlanInvalid) as e:
        session.start_run("pf1", {})
    assert "Asp45–Arg112" in e.value.errors[0] and "Pick the AdK residues" in e.value.errors[0]
    assert "run_id" not in session.PLANS["pf1"]  # still unclaimed


def test_a_methods_pdf_shows_its_embedded_figure(tmp_path):
    """A PDF-only upload: the figure shown beside ours is the PDF's embedded image, as PNG (an <img> cannot
    show a PDF). The digest stays the uploaded file's, so recorded runs still match the same upload."""
    import hashlib
    import io
    from PIL import Image
    from pypdf import PdfWriter
    img = Image.new("RGB", (600, 380), "white")
    buf = io.BytesIO()
    img.save(buf, "PDF")  # a one-page PDF that embeds a 600 x 380 image
    pdf = buf.getvalue()
    fig = figure.save_figure([("paper.pdf", "application/pdf", pdf)], directory=tmp_path)
    raw, mime = figure.load_figure(fig["figure_id"], directory=tmp_path)
    assert mime == "image/png" and Image.open(io.BytesIO(raw)).size == (600, 380)
    assert fig["sha256"] == hashlib.sha256(pdf).hexdigest()
    w = PdfWriter()
    w.add_blank_page(200, 200)
    out = io.BytesIO()
    w.write(out)
    blank = figure.save_figure([("blank.pdf", "application/pdf", out.getvalue())], directory=tmp_path)
    assert blank["mime"] == "application/pdf"  # no embedded figure: the PDF itself is kept


# ---------- planner windows: one window unless comparing ----------

def test_an_empty_second_window_is_dropped_for_a_single_window_analysis():
    """The live bug: paper 01 ("last 500 ns") came back with window_b [4187, 4187] and planning failed."""
    from app.agent.planner import repair_windows, validate_plan
    eq = {**PLAN, "dataset": "adk_equilibrium", "window_a": [2104, 4187], "window_b": [4187, 4187],
          "steps": [{"tool": "calculate_contacts", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]}
    fixed = repair_windows(eq)
    assert fixed["window_b"] is None and fixed["window_a"] == [2104, 4187] and validate_plan(fixed) == []
    assert repair_windows({**eq, "window_a": [2104, 9999]})["window_a"] == [2104, 4187]  # clamped to the run
    # A comparison keeps its (empty) second window, so the model's retry, then the user, sees the problem.
    cmp = {**eq, "steps": [{"tool": "contact_difference_map", "rationale": ""}, {"tool": "submit_report", "rationale": ""}]}
    assert repair_windows(cmp)["window_b"] == [4187, 4187] and validate_plan(repair_windows(cmp))
    assert any("need two windows" in e for e in validate_plan({**cmp, "window_b": None}))
    # A valid second window is left alone even without a comparison ('all' spans both windows).
    assert repair_windows({**eq, "window_a": [0, 2093], "window_b": [2093, 4187]})["window_b"] == [2093, 4187]


# ---------- fixes after live run 20261104-014851 ----------

def test_a_transfer_runs_on_the_data_the_paper_used_not_a_leftover_picker():
    spec = {"mode": "transfer", "dataset": "none", "closest_dataset": "adk_equilibrium"}
    assert figure.transfer_dataset(spec, "adk_transition") == "adk_equilibrium"
    assert figure.transfer_dataset({**spec, "closest_dataset": "none"}, "adk_transition") == "adk_transition"
    assert "closest_dataset" in figure.spec_schema()["schema"]["required"]


def test_the_approved_radius_is_not_a_bare_number():
    """The live native-contacts sample ended in needs_review: the finding wrote "8.0 Å" (the approved radius)."""
    from app.agent.report import bare_numbers
    s = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98], steps=PLAN["steps"]))
    assert bare_numbers(s, "Q1 and Q2 with Cα atoms within 8.0 Å, as published.") == []
    s6 = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98], native_radius=6.0, steps=PLAN["steps"]))
    assert bare_numbers(s6, "radius 6 Å") == [] and bare_numbers(s6, "radius 8.0 Å") == ["8.0"]
    plain = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98],
                                  steps=[{"tool": "calculate_contacts"}, {"tool": "submit_report"}]))
    assert bare_numbers(plain, "within 8.0 Å") == ["8.0"]  # only native-contacts plans approve a radius


def test_a_check_that_could_not_run_can_be_run_again(monkeypatch, tmp_path):
    import app.agent.orchestrator as orch
    from app.agent import session, verifier
    monkeypatch.setattr(orch, "INVESTIGATIONS_DIR", tmp_path)
    run = orch.new_run("q", ApprovedPlan("adk_transition", "LID", "CORE", 4.5, [0, 98], steps=PLAN["steps"]))
    run.state.report = {"findings": []}
    run.verification = {"status": "error", "error": "NotFoundError: Error code: 404 - The requested model was not found."}
    run.save()
    calls = []
    monkeypatch.setattr(verifier, "verify_run", lambda r: (calls.append(r.run_id), verifier.settle(r, {"status": "verified", "flags": []}, "verified")))
    session.reverify(run.run_id)  # read back from disk, as after a restart
    for _ in range(50):
        if session.run_view(run.run_id)["verification"]["status"] == "verified":
            break
        import time; time.sleep(0.05)
    assert calls == [run.run_id] and session.run_view(run.run_id)["status"] == "verified"
    with pytest.raises(session.CannotReverify):
        session.reverify(run.run_id)  # a finished check is not re-run
    session.RUNS.pop(run.run_id, None)


# ---------- a figure's residue ranges limit what is tested (paper 01: LID 122-159 x CORE 100-121) ----------

def test_residue_ranges_limit_the_pairs_counted_and_tested():
    from app.science.contacts import calculate_contacts
    from app.science.residues import parse_residue_key
    full = calculate_contacts("adk_transition", "LID", "CORE", 4.0, sequence_exclusion=3)
    cut = calculate_contacts("adk_transition", "LID", "CORE", 4.0, sequence_exclusion=3, ranges={"CORE": [100, 121]})
    rb = [parse_residue_key(k).resid for k in cut["keys_b"]]
    assert min(rb) == 100 and max(rb) == 121 and len(cut["keys_a"]) == len(full["keys_a"])
    assert cut["frequency"].shape == (len(cut["keys_a"]), 22)
    j = full["keys_b"].index(cut["keys_b"][0])  # the same pair has the same value either way
    assert (cut["frequency"][:, 0] == full["frequency"][:, j]).all()


def test_a_contact_map_figure_sets_its_axis_ranges_on_the_plan():
    spec = {"kind": "contact_map", "display": {
        "x": {"group": "LID", "residue_min": 122, "residue_max": 159}, "y": {"group": "CORE", "residue_min": 100, "residue_max": 121}}}
    assert figure.figure_ranges(spec, {"group_a": "LID", "group_b": "CORE"}) == {"LID": [122, 159], "CORE": [100, 121]}
    assert figure.figure_ranges({**spec, "kind": "rmsf"}, {"group_a": "LID", "group_b": "CORE"}) is None
    from app.agent.planner import to_approved, validate_plan
    a = to_approved({**PLAN, "residue_ranges": {"CORE": [100, 121]}})
    assert a.residue_ranges == {"CORE": [100, 121]}
    assert validate_plan({**PLAN, "residue_ranges": {"NMP": [30, 59]}})  # not an approved group
    assert validate_plan({**PLAN, "residue_ranges": {"CORE": [130, 150]}})  # no CORE residues there


def test_a_pair_outside_the_ranges_is_refused():
    from app.agent.tools import resolve_pair
    s = RunState(ApprovedPlan("adk_transition", "LID", "CORE", 4.0, [0, 98], residue_ranges={"CORE": [100, 121]},
                              steps=[{"tool": "distance_over_time"}, {"tool": "submit_report"}]))
    assert run_tool(s, "distance_over_time", '{"pair": "THR155-GLN160"}')["status"] == "refused"
    assert resolve_pair(s.plan, "LYS136-ASP118")[1].endswith(":118:ASP")
