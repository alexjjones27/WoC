"""Tests for the longshot shrink (aggregation.py step 8).

The bug these pin: shrinking the sub-threshold buckets and then leaning on
step 3's renormalization does NOT deliver the calibrated factor, because
renormalizing scales the shrunk buckets back up along with everything else.
The correction has to redistribute onto the non-longshot buckets explicitly.
"""
import pytest

from aggregation import LONGSHOT_PROB_THRESHOLD, _longshot_corrected_probs
from common.distribution import PriceBucket


def _buckets(probs):
    return [PriceBucket(low=100.0 * i, high=100.0 * (i + 1), prob=p) for i, p in enumerate(probs)]


def test_delivers_exactly_the_requested_factor():
    """A longshot bucket must end at `shrink` times its normalized
    probability -- that is what the backtest measured."""
    probs = [0.05] * 8 + [0.30, 0.30]  # 0.40 longshot mass, 0.60 rest
    out, realized = _longshot_corrected_probs(_buckets(probs), 0.16)
    assert sum(out) == pytest.approx(1.0)
    assert out[0] / 0.05 == pytest.approx(0.16)
    assert realized == pytest.approx(0.16)


def test_renormalize_only_would_have_under_delivered():
    """Guard against a regression back to the old behaviour: with 40% of the
    mass in longshots, 'shrink then renormalize' delivers 0.24, not 0.16."""
    probs = [0.05] * 8 + [0.30, 0.30]
    naive = [p * 0.16 if p < LONGSHOT_PROB_THRESHOLD else p for p in probs]
    naive = [p / sum(naive) for p in naive]
    assert naive[0] / 0.05 == pytest.approx(0.241, abs=0.001)  # the old, wrong number

    out, _ = _longshot_corrected_probs(_buckets(probs), 0.16)
    assert out[0] / 0.05 == pytest.approx(0.16)
    assert out[0] < naive[0]


@pytest.mark.parametrize("longshot_mass", [0.1, 0.2, 0.4, 0.6, 0.8])
@pytest.mark.parametrize("shrink", [0.16, 0.20, 0.30, 0.74])
def test_factor_is_independent_of_how_much_mass_sits_in_the_tail(longshot_mass, shrink):
    """The old bug's severity scaled with tail mass, so the fix has to hold
    across the whole range, not just one convenient case."""
    n_ls = 10
    probs = [longshot_mass / n_ls] * n_ls + [1.0 - longshot_mass]
    assert probs[0] < LONGSHOT_PROB_THRESHOLD
    out, realized = _longshot_corrected_probs(_buckets(probs), shrink)
    assert sum(out) == pytest.approx(1.0)
    assert out[0] / probs[0] == pytest.approx(shrink)
    assert realized == pytest.approx(shrink)


def test_mass_moves_onto_the_non_longshot_buckets_only():
    probs = [0.05, 0.05, 0.45, 0.45]
    out, _ = _longshot_corrected_probs(_buckets(probs), 0.20)
    assert out[0] == pytest.approx(0.05 * 0.20)
    assert out[2] > 0.45 and out[3] > 0.45
    assert out[2] == pytest.approx(out[3])


def test_unnormalized_input_is_handled_on_the_normalized_scale():
    """Adapters are allowed to hand over buckets that don't sum to 1."""
    probs = [0.04] * 6 + [0.40, 0.40]
    total = sum(probs)
    out, realized = _longshot_corrected_probs(_buckets(probs), 0.30)
    assert sum(out) == pytest.approx(1.0)
    assert out[0] / (0.04 / total) == pytest.approx(0.30)
    assert realized == pytest.approx(0.30)


def test_all_longshot_reports_no_correction_rather_than_pretending():
    """With nothing above threshold there is nothing to redistribute onto,
    so the shrink is a uniform scaling that normalization undoes exactly.
    Report 1.0 instead of claiming a correction that cannot land."""
    out, realized = _longshot_corrected_probs(_buckets([0.05] * 20), 0.16)
    assert realized == 1.0
    assert out == pytest.approx([0.05] * 20)  # already normalized, so unchanged


def test_disabled_correction_is_a_no_op():
    probs = [0.05] * 8 + [0.3, 0.3]
    out, realized = _longshot_corrected_probs(_buckets(probs), 1.0)
    assert realized == 1.0
    assert out == pytest.approx(probs)


def test_output_is_always_normalized_whichever_path_is_taken():
    """Every return path -- corrected, all-longshot, and disabled -- hands
    back the same normalized contract, so callers never have to ask which
    branch ran."""
    for probs, shrink in [
        ([0.05] * 8 + [0.3, 0.3], 0.16),   # corrected
        ([0.04] * 25, 0.16),                # all longshot, nothing to move mass onto
        ([0.05] * 8 + [0.3, 0.3], 1.0),     # correction disabled
        ([0.04] * 6 + [0.4, 0.4], 0.3),     # unnormalized input
    ]:
        out, _ = _longshot_corrected_probs(_buckets(probs), shrink)
        assert sum(out) == pytest.approx(1.0)


def test_empty_and_zero_mass_inputs_do_not_blow_up():
    assert _longshot_corrected_probs([], 0.16) == ([], 1.0)
    out, realized = _longshot_corrected_probs(_buckets([0.0, 0.0]), 0.16)
    assert realized == 1.0
    assert out == [0.0, 0.0]


def test_negative_probabilities_are_clamped_not_propagated():
    out, _ = _longshot_corrected_probs(_buckets([-0.01, 0.5, 0.51]), 0.2)
    assert all(p >= 0.0 for p in out)
    assert sum(out) == pytest.approx(1.0)
