from enum import StrEnum


class ProvenanceClass(StrEnum):
    USER_SUPPLIED = "user_supplied"
    SYNTHETIC = "synthetic"
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"


class RightsAccessClass(StrEnum):
    PRIVATE = "private"
    INTERNAL_TEST = "internal_test"
    REDISTRIBUTABLE = "redistributable"


class RetentionClass(StrEnum):
    EPHEMERAL = "ephemeral"
    PROJECT = "project"
    LEGAL_HOLD = "legal_hold"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    EVIDENCE_READY = "evidence_ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class RepeatabilityClass(StrEnum):
    D0 = "D0"
    D1 = "D1"
    D2 = "D2"
