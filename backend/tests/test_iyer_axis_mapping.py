"""The Iyer et al. 2020 Fig 5C axis-mapping check (scripts/iyer_axis_mapping.py).

Fast tests read the committed figure and test the matching logic. The slow test downloads the two PDB models
and confirms the mapping: alignment position p (0-based) = E. coli AdK residue p + 1.
"""
import urllib.error

import pytest

from scripts import iyer_axis_mapping as m


@pytest.fixture(scope="module")
def figure():
    img = m.load_rgb()
    return img, m.find_frame(img)


def test_frame_is_square_and_inside_the_image(figure):
    img, f = figure
    h, w, _ = img.shape
    assert 0 < f.left < f.right < w and 0 < f.top < f.bottom < h
    assert abs((f.right - f.left) - (f.bottom - f.top)) <= 3  # a square matrix plot


def test_reads_the_red_and_blue_dots(figure):
    dots = m.read_dots(*figure)
    assert len(dots["red"]) == 46 and len(dots["blue"]) == 30


def test_positions_span_the_matrix(figure):
    img, f = figure
    pts = m.to_positions(m.read_dots(img, f)["red"], f, 214)
    assert all(-1 <= x <= 214 and -1 <= y <= 214 for x, y in pts)


def test_pixel_to_position_cell_centres():
    f = m.Frame(left=0, right=100, top=0, bottom=100)
    assert m.to_positions([(5.0, 95.0)], f, 10) == [(0.0, 9.0)]


def test_count_matches_uses_both_triangles_and_the_shift():
    pairs = [(2, 7)]
    assert m.count_matches([(2, 7), (7, 2)], pairs, 0) == 2
    assert m.count_matches([(3, 8)], pairs, 1) == 1
    assert m.count_matches([(3, 8)], pairs, 0) == 1  # within one cell
    assert m.count_matches([(5, 10)], pairs, 0) == 0


@pytest.mark.slow
def test_mapping_is_residue_plus_one(tmp_path):
    try:
        r = m.check(pdb_dir=tmp_path)
    except urllib.error.URLError as e:
        pytest.skip(f"RCSB not reachable: {e}")
    assert r["n_positions"] == 214 and r["resids"] == list(range(1, 215))
    for c, n in r["dots"].items():
        counts = r["matches"][c]
        assert counts[0] == n  # every dot lands on a recomputed contact at shift 0
        assert counts[-1] < n and counts[1] < n
    cs = r["contacts"]
    assert (57, 169) in cs["closed_only"]  # Leu58-Glu170, named in the article
