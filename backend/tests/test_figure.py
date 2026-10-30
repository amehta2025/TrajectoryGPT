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

SPEC = {"summary": "Q1 and Q2 along the AdK transition path", "kind": "native_contacts", "mode": "exact",
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
