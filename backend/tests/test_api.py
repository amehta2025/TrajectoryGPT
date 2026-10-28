"""Endpoint tests on adk_transition (fast)."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app

DS = "adk_transition"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "adk_transition" in r.json()["datasets"]


def test_metadata(client):
    m = client.get(f"/metadata/{DS}").json()
    assert m["n_frames"] == 98
    assert m["n_residues"] == 214
    assert m["dt_ns"] is None and m["time_label"] == "path step"
    assert m["stats_valid"] is False
    assert m["playback_stride"] == 1
    assert set(m["groups"]) == {"NMP", "LID", "CORE"}


def test_unknown_dataset_is_404(client):
    assert client.get("/metadata/nope").status_code == 404


def test_contacts(client):
    r = client.get(f"/contacts/{DS}", params={"group_a": "NMP", "group_b": "CORE"}).json()
    freq = np.array(r["frequency"])
    excl = np.array(r["excluded"])
    assert freq.shape == (len(r["keys_a"]), len(r["keys_b"])) == excl.shape
    assert excl.any()  # NMP 30 next to CORE 29 is sequence-local
    assert ((freq >= 0) & (freq <= 1)).all()
    assert r["units"]["time"] == "path step"


def test_contacts_bad_cutoff_is_400(client):
    r = client.get(f"/contacts/{DS}", params={"group_a": "NMP", "group_b": "CORE", "cutoff": 50})
    assert r.status_code == 400
    assert "cutoff" in r.json()["detail"]


def test_distances(client):
    r = client.get(f"/distances/{DS}", params={
        "key_a": "adk_transition:4AKE:158:ASP", "key_b": "adk_transition:4AKE:36:ARG", "stride": 2,
    }).json()
    assert len(r["frames"]) == len(r["distance"]) == 49
    assert r["time_ns"] is None  # no physical time on the transition path


def test_structure_and_frames_agree(client):
    pdb = client.get(f"/structure/{DS}").text
    n_pdb = sum(line.startswith("ATOM") for line in pdb.splitlines())
    r = client.get(f"/frames/{DS}", params={"start": 0, "stop": 10, "stride": 3})
    assert r.headers["X-Frames"] == "0,3,6,9"
    n_atoms = int(r.headers["X-N-Atoms"])
    assert n_atoms == n_pdb
    coords = np.frombuffer(r.content, dtype="<f4")
    assert coords.size == 4 * n_atoms * 3


def test_frames_are_gzipped(client):
    r = client.get(f"/frames/{DS}", params={"stop": 5}, headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_bad_stride_rejected(client):
    assert client.get(f"/frames/{DS}", params={"stride": 0}).status_code == 422


def test_compare_refused_on_transition(client):
    r = client.get(f"/compare/{DS}", params={"group_a": "LID", "group_b": "NMP",
                                             "a_start": 0, "a_stop": 49, "b_start": 49, "b_stop": 98})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "refused" and "biased" in body["reason"]


def test_contact_stats_refused_on_transition(client):
    r = client.get(f"/contact_stats/{DS}", params={
        "key_a": "adk_transition:4AKE:158:ASP", "key_b": "adk_transition:4AKE:36:ARG"}).json()
    assert r["status"] == "refused"


def test_native_contacts(client):
    r = client.get(f"/native_contacts/{DS}").json()
    assert r["ref_frames"] == [0, 97]
    assert len(r["q"]) == 2 and len(r["q"][0]) == len(r["frames"]) == 98
    assert r["q"][0][0] == 1.0 and r["q"][1][-1] == 1.0


def test_native_contacts_bad_radius_is_400(client):
    assert client.get(f"/native_contacts/{DS}", params={"radius": 50}).status_code == 400


@pytest.mark.slow
def test_compare_on_equilibrium(client):
    r = client.get("/compare/adk_equilibrium", params={"group_a": "LID", "group_b": "CORE",
                                                       "a_start": 0, "a_stop": 2093, "b_start": 2093, "b_stop": 4187}).json()
    assert r["status"] == "ok"
    assert r["n_tested"] == len(r["rows"]) and r["n_supported"] > 0
