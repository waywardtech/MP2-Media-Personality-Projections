from pathlib import Path

from mp2_storage.object_store import sha256_file


def test_d0_hash_repeatability(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.write_bytes(bytes(range(255)))
    assert sha256_file(fixture) == sha256_file(fixture)
