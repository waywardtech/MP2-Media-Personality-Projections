from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class ObjectInfo:
    bucket: str
    key: str
    size_bytes: int
    sha256: str


class ObjectStore(Protocol):
    def put_file(self, bucket: str, key: str, source: Path) -> ObjectInfo: ...
    def open(self, bucket: str, key: str) -> BinaryIO: ...
    def exists(self, bucket: str, key: str) -> bool: ...


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class LocalObjectStore:
    """Filesystem adapter for tests/local-only mode; blocks traversal outside its root."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, bucket: str, key: str) -> Path:
        candidate = (self.root / bucket / key).resolve()
        if self.root not in candidate.parents:
            raise ValueError("object path escapes store root")
        return candidate

    def put_file(self, bucket: str, key: str, source: Path) -> ObjectInfo:
        source = source.resolve(strict=True)
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(source)
        if not target.exists() or sha256_file(target) != digest:
            shutil.copyfile(source, target)
        return ObjectInfo(bucket, key, source.stat().st_size, digest)

    def open(self, bucket: str, key: str) -> BinaryIO:
        return self._path(bucket, key).open("rb")

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).is_file()


class S3ObjectStore:
    """S3-compatible object store, tuned for multi-gigabyte media.

    Transfer settings are not incidental here. MP2's whole domain is large media, and
    boto3's defaults are wrong for it: an 8 MiB chunk turns an 18 GB film master into ~2,300
    multipart parts, which is enough to make a SeaweedFS gateway drop the connection. That
    failure is expensive because it surfaces only after the entire file has been read and
    almost entirely uploaded.

    Larger chunks with modest concurrency keep the part count in the low hundreds and bound
    in-flight memory to `chunk x concurrency`, which has to fit the API container's limit.
    """

    #: 128 MiB chunks: an 18 GB master becomes ~144 parts instead of ~2,300.
    DEFAULT_CHUNK_BYTES = 128 * 1024 * 1024
    #: Concurrency is deliberately low. Each thread buffers a chunk, so this multiplied by
    #: the chunk size is the peak resident memory of an upload.
    DEFAULT_CONCURRENCY = 2

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, region: str,
                 chunk_bytes: int | None = None, max_concurrency: int | None = None):
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config

        chunk = int(os.getenv("MP2_S3_CHUNK_BYTES", chunk_bytes or self.DEFAULT_CHUNK_BYTES))
        threads = int(os.getenv("MP2_S3_CONCURRENCY",
                                max_concurrency or self.DEFAULT_CONCURRENCY))

        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
            config=Config(
                # A long upload against a busy local gateway needs patience, and a
                # transient close mid-transfer should be retried rather than failing a
                # multi-gigabyte ingest outright.
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=30, read_timeout=300, max_pool_connections=threads + 4,
            ),
        )
        self.transfer_config = TransferConfig(
            multipart_threshold=chunk,
            multipart_chunksize=chunk,
            max_concurrency=threads,
            use_threads=True,
        )
        for bucket in ("mp2-raw", "mp2-normalized", "mp2-derived", "mp2-model-cache",
                       "mp2-eval", "mp2-backup-staging"):
            try:
                self.client.head_bucket(Bucket=bucket)
            except Exception:
                self.client.create_bucket(Bucket=bucket)

    def put_file(self, bucket: str, key: str, source: Path) -> ObjectInfo:
        digest = sha256_file(source)
        self.client.upload_file(str(source), bucket, key,
                                ExtraArgs={"Metadata": {"sha256": digest}},
                                Config=self.transfer_config)
        return ObjectInfo(bucket, key, source.stat().st_size, digest)

    def open(self, bucket: str, key: str) -> BinaryIO:
        # botocore returns a StreamingBody, which satisfies BinaryIO for our read paths.
        body: BinaryIO = self.client.get_object(Bucket=bucket, Key=key)["Body"]
        return body

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False
