
from types import SimpleNamespace

from app.services.compliance import _build_sources


def test_build_sources_prioritizes_mandatory_requirements():
    rows = []

    # More than 40 ordinary passages should not crowd out
    # mandatory bid-stage requirements.
    for index in range(50):
        rows.append((
            SimpleNamespace(
                source_ref={"page": index + 1, "block_index": index},
                unit_type="text",
            ),
            "Tender.pdf",
        ))

    rows.append((
        SimpleNamespace(
            source_ref={"page": 34, "block_index": 1},
            unit_type="text",
        ),
        "Tender.pdf",
    ))

    # Replace the final ordinary passage with the actual mandatory text.
    from unittest.mock import patch

    original_text = {
        id(unit): f"General tender information passage {index}"
        for index, (unit, _) in enumerate(rows[:-1])
    }
    original_text[id(rows[-1][0])] = (
        "Bidder shall submit a Bid Security Declaration "
        "with the tender submission."
    )

    with patch(
        "app.services.compliance._unit_text",
        side_effect=lambda unit: original_text[id(unit)],
    ):
        sources, context, source_content = _build_sources(
            rows,
            role="tender",
            prefix="T",
        )

    assert len(sources) == 40
    assert "Bid Security Declaration" in context
    assert any(
        "Bid Security Declaration" in content
        for content in source_content.values()
    )


def test_build_sources_balances_documents_and_deduplicates_reprocessed_passages():
    rows = []
    for filename in ("NIT.pdf", "TR Format.pdf", "BOQ.xlsx"):
        for index in range(20):
            unit = SimpleNamespace(
                source_ref={"page": index + 1, "block_index": index},
                unit_type="text",
                content=f"{filename} evidence passage {index}",
            )
            rows.append((unit, filename))
            if index == 0:
                # Simulate the same page/block being present from an older
                # completed processing run. It should not consume a source slot.
                duplicate = SimpleNamespace(
                    source_ref={"page": 1, "block_index": 0},
                    unit_type="text",
                    content=f"{filename} evidence passage 0",
                )
                rows.append((duplicate, filename))

    from unittest.mock import patch

    with patch(
        "app.services.compliance._unit_text",
        side_effect=lambda unit: unit.content,
    ):
        sources, context, source_content = _build_sources(
            rows,
            role="tender",
            prefix="T",
        )

    assert len(sources) == 40
    assert len(source_content) == 40
    assert len({source.source_id for source in sources}) == 40

    selected_by_document = {
        filename: sum(source.document_name == filename for source in sources)
        for filename in ("NIT.pdf", "TR Format.pdf", "BOQ.xlsx")
    }
    assert set(selected_by_document) == {"NIT.pdf", "TR Format.pdf", "BOQ.xlsx"}
    assert max(selected_by_document.values()) - min(selected_by_document.values()) <= 1
    assert "NIT.pdf" in context
    assert "TR Format.pdf" in context
    assert "BOQ.xlsx" in context
