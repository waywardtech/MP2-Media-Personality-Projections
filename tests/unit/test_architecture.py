import ast
from pathlib import Path


def test_canonical_packages_do_not_import_provider_sdks() -> None:
    forbidden = {"openai", "anthropic", "google", "boto3", "temporalio"}
    roots = [Path("packages/domain"), Path("packages/schemas")]
    violations: list[str] = []
    for root in roots:
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(name.split(".")[0] in forbidden for name in names):
                    violations.append(f"{source}: {names}")
    assert not violations


def test_all_required_domain_tables_exist() -> None:
    from mp2_domain.models import Base
    required = {"media_work", "media_edition", "source_asset", "segment", "analysis_run",
        "extractor_definition", "measurement", "evidence_claim", "model_execution",
        "genome_version", "genome_value", "projection", "viewer_profile", "viewer_signal",
        "viewer_dimension", "viewer_state", "viewing_context", "recommendation_run",
        "recommendation_candidate", "user_feedback"}
    assert required <= set(Base.metadata.tables)
