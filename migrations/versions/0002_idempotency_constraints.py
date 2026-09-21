"""Natural-key uniqueness so retried activities cannot duplicate canonical output.

Application code already upserts on these keys. These constraints make the guarantee
structural rather than conventional: if two workers ever race the same activity after a
crash, the database refuses the duplicate instead of silently producing two canonical rows.

NULLS NOT DISTINCT (PostgreSQL 15+) is required because work-level measurements and
claims carry a NULL segment_id, which would otherwise bypass the constraint entirely.
"""

from alembic import op

revision = "0002_idempotency"
down_revision = "0001_m0_m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE segment
        ADD CONSTRAINT uq_segment_natural_key
        UNIQUE (source_asset_id, kind, start_ms, end_ms)
    """)
    op.execute("""
        ALTER TABLE measurement
        ADD CONSTRAINT uq_measurement_natural_key
        UNIQUE NULLS NOT DISTINCT
        (analysis_run_id, segment_id, extractor_definition_id, metric)
    """)
    op.execute("""
        ALTER TABLE evidence_claim
        ADD CONSTRAINT uq_evidence_claim_natural_key
        UNIQUE NULLS NOT DISTINCT (analysis_run_id, segment_id, claim_type)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE evidence_claim DROP CONSTRAINT uq_evidence_claim_natural_key")
    op.execute("ALTER TABLE measurement DROP CONSTRAINT uq_measurement_natural_key")
    op.execute("ALTER TABLE segment DROP CONSTRAINT uq_segment_natural_key")
