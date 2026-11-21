# Third-party inventory

Runtime dependency licenses must be verified from the exact resolved lock/SBOM before distribution. Expected foundations include PostgreSQL (PostgreSQL), pgvector (PostgreSQL), Temporal (MIT), SeaweedFS (Apache-2.0), FastAPI (MIT), SQLAlchemy (MIT), React (MIT), Vite (MIT), FFmpeg (LGPL/GPL depending build), OpenCV (Apache-2.0), PySceneDetect (BSD-3-Clause), librosa (ISC), faster-whisper (MIT), CTranslate2 (MIT), sentence-transformers (Apache-2.0), llama.cpp (MIT), OpenTelemetry (Apache-2.0), Prometheus (Apache-2.0), and Grafana (AGPL-3.0).

Generate image SBOMs after pulls:

```bash
./infra/scripts/lock-images.sh
./infra/scripts/generate-sbom.sh
```

No model weights are included in this repository. Each selected model requires a separate license record.
