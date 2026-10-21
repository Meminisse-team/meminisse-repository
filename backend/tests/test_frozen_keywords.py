from pathlib import Path
import pytest
from evals.frozen_keywords import load_fixture
from evals.information_preservation import compute_recall_curve

FIXTURES = Path(__file__).parents[1] / "evals" / "fixtures"

def test_frozen_ground_truth_is_repeatable_and_rejects_source_drift():
    source = (FIXTURES / "regression-source.txt").read_text()
    fixture = load_fixture(FIXTURES / "regression-keywords.json", source)
    result = compute_recall_curve(fixture.keywords, source, cutoffs=(3, 6))
    assert result == compute_recall_curve(fixture.keywords, source, cutoffs=(3, 6))
    assert result["6"]["survived"] == 6
    with pytest.raises(ValueError, match="hash mismatch"):
        load_fixture(FIXTURES / "regression-keywords.json", source + "changed")
