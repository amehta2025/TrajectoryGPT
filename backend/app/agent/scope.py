"""Scope gate before planning (red-team A1-A4, G4): nonsense, off-topic, other-protein and creative requests
get a polite fixed reply with example questions. No plan is made and no tools run.

Two stages: a free check in code (no letters at all, e.g. "?" or "..."), then one small structured call at
low effort that only classifies the question. The reply text is ours, never the model's.

The classifier is unsure about short or terse questions (red-team Round 2: "contacts", "RMSF?", "Explain Q1 and
Q2"). If it calls one "unclear" or "needs_context" but the question names something this app analyses, code lets
it through; a question about a figure that wasn't attached gets "attach the figure". Every decline except "no
letters" offers "Ask anyway", which plans the question without the gate.
"""
import re

from app.agent import prompts
from app.agent.llm import EFFORT, LLM

VERDICTS = ["answerable", "unclear", "off_topic", "other_protein", "creative", "needs_context"]
# Words that only make sense as a question about these simulations.
APP_TERMS = re.compile(r"\b(LID|NMP|CORE|AdK|adenylate|kinase|contacts?|native|Q[12]|RMSF|flexib\w*|cutoffs?|"
                       r"frames?|distances?|opening|domains?|residues?|autocorrelation|N_eff|trajector\w*|"
                       r"simulations?|equilibrium|transition)\b", re.I)
FIGURE = re.compile(r"\b(figures?|plots?|graphs?|images?|panels?)\b", re.I)

EXAMPLES = [
    "Which LID–CORE contacts change between the first and second half of the run?",
    "Is the drop in the SER30–ASP84 contact between the first and second half of the run real?",
    "How open is the LID domain over the course of the run?",
]

MESSAGES = {
    "unclear": "I couldn't tell what you'd like to know. Try a question about the simulation, for example:",
    "off_topic": "That's outside what I can help with. TrajectoryGPT analyses simulations of one enzyme, "
                 "adenylate kinase (AdK). Try, for example:",
    "other_protein": "I only have simulations of adenylate kinase (AdK), so I can't answer questions about other "
                     "proteins. Try, for example:",
    "creative": "I only run analyses on the adenylate kinase simulations, so I can't write that. Try, for example:",
    "needs_context": "There's no earlier investigation in this session for that to refer to. Ask a full question "
                     "first, for example:",
    "needs_figure": "That question is about a figure, but none is attached. Drop the figure (PNG, JPEG or PDF) "
                    "into the box with your question, or try, for example:",
}


def schema() -> dict:
    return {"name": "ScopeVerdict", "schema": {
        "type": "object", "additionalProperties": False, "required": ["verdict", "why"],
        "properties": {"verdict": {"type": "string", "enum": VERDICTS},
                       "why": {"type": "string", "description": "A few words, for logs only."}}}}


def precheck(question: str) -> str | None:
    """'unclear' for text with no letters at all; None means ask the classifier."""
    return None if any(c.isalpha() for c in question) else "unclear"


def in_scope(question: str) -> bool:
    """True when the question names something this app analyses: a domain, a residue, contacts, RMSF, Q1/Q2…"""
    from app.agent.limits import residue_mentions
    return bool(APP_TERMS.search(question) or residue_mentions(question))


def declined(verdict: str, why: str = "", ask_anyway: bool = True) -> dict:
    return {"verdict": verdict, "message": MESSAGES[verdict], "examples": EXAMPLES, "why": why,
            "ask_anyway": ask_anyway}


def check(llm: LLM, question: str, has_context: bool = False) -> tuple[dict | None, list]:
    """(None, usages) when the question can be planned, else (declined reply, usages)."""
    verdict = precheck(question)
    if verdict:
        return declined(verdict, "no letters", ask_anyway=False), []
    r = llm.run_turn([{"role": "system", "content": prompts.SCOPE_SYSTEM},
                      {"role": "user", "content": prompts.scope_user(question, has_context)}],
                     schema=schema(), effort=EFFORT["scope"])
    p = r.parsed if isinstance(r.parsed, dict) else {}
    verdict = p.get("verdict")
    if verdict == "needs_context" and has_context:
        verdict = "answerable"  # the follow-up context resolves "that"
    elif verdict in ("unclear", "needs_context"):
        if FIGURE.search(question):
            verdict = "needs_figure"  # "does my figure match…" typed without the figure
        elif in_scope(question):
            verdict = "answerable"  # terse but about this app ("contacts", "RMSF?"): the plan shows what it means
    if verdict not in MESSAGES:
        return None, [r.usage]  # answerable (or an unexpected reply): plan as usual
    return declined(verdict, str(p.get("why", ""))[:200]), [r.usage]
