"""Unit coverage for Azure continuous-recognition utterance assembly."""

from voice.cli.live_run import _combine_final_transcripts
from voice.events import Transcript


def test_combines_multiple_azure_final_segments_conservatively() -> None:
    combined = _combine_final_transcripts([
        Transcript(
            "Sa është tarifa",
            final=True,
            confidence=0.91,
            alternatives=("Sa është norma",),
            provider="azure",
            started_s=1.0,
            finalized_s=2.0,
            diagnostics={"offset": 10},
        ),
        Transcript(
            "për shlyerje para afatit?",
            final=True,
            confidence=0.84,
            critical_confidences={"shlyerje": 0.88},
            provider="azure",
            started_s=1.0,
            finalized_s=3.0,
            diagnostics={"offset": 20},
        ),
    ])

    assert combined.text == "Sa është tarifa për shlyerje para afatit?"
    assert combined.confidence == 0.84
    assert combined.alternatives == (
        "Sa është norma për shlyerje para afatit?",
    )
    assert combined.critical_confidences == {"shlyerje": 0.88}
    assert combined.finalized_s == 3.0
    assert combined.diagnostics["segment_count"] == 2


def test_combined_confidence_is_unavailable_if_any_segment_lacks_it() -> None:
    combined = _combine_final_transcripts([
        Transcript("Pjesa e parë", final=True, confidence=0.9, provider="azure"),
        Transcript("pjesa e dytë", final=True, confidence=None, provider="azure"),
    ])

    assert combined.confidence is None
