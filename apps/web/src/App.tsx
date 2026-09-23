import { useCallback, useEffect, useState } from "react";
import {
  api,
  PROVENANCE_CLASSES,
  RETENTION_CLASSES,
  RIGHTS_CLASSES,
  type AnalysisRun,
  type MediaWork,
  type RunEvidence,
} from "./api";

type Page =
  | "status"
  | "ingest"
  | "runs"
  | "evidence"
  | "profile"
  | "tonight"
  | "recommendations"
  | "works";

const PAGES: [Page, string][] = [
  ["status", "Status"],
  ["ingest", "Media ingest"],
  ["runs", "Analysis runs"],
  ["evidence", "Evidence browser"],
  ["works", "Work explorer"],
  ["profile", "Profile"],
  ["tonight", "Tonight"],
  ["recommendations", "Recommendations"],
];

const SCAFFOLD_PAGES: Page[] = ["profile", "tonight", "recommendations"];

function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    setLoading(true);
    loader()
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(reload, [reload]);
  return { data, error, loading, reload };
}

function StatusPage() {
  const health = useAsync(() => api.health());
  const ready = useAsync(() => api.ready());
  const runs = useAsync(() => api.listRuns());

  return (
    <>
      <div className="grid">
        <div className="metric">
          <span>API health</span>
          <strong>{health.error ? "unavailable" : (health.data?.status ?? "…")}</strong>
        </div>
        <div className="metric">
          <span>Readiness (database)</span>
          <strong>{ready.error ? "not ready" : (ready.data?.status ?? "…")}</strong>
        </div>
        <div className="metric">
          <span>Analysis runs</span>
          <strong>{runs.data?.length ?? "…"}</strong>
        </div>
      </div>
      <p className="muted">
        The M0–M4 console reports operational state and recorded evidence. It does not
        present psychological conclusions: genome calculation and projection design are
        deliberately out of scope until calibration is done.
      </p>
    </>
  );
}

function IngestPage() {
  const media = useAsync(() => api.listMedia());
  const [title, setTitle] = useState("Synthetic AV Fixture");
  const [workType, setWorkType] = useState("short");
  const [path, setPath] = useState("/media/synthetic-av.mp4");
  const [provenance, setProvenance] = useState<string>(PROVENANCE_CLASSES[0]);
  const [rights, setRights] = useState<string>(RIGHTS_CLASSES[0]);
  const [retention, setRetention] = useState<string>(RETENTION_CLASSES[0]);
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const say = (line: string) => setLog((prior) => [...prior, line]);

  const submit = async () => {
    setBusy(true);
    setLog([]);
    try {
      const work = await api.createMedia({
        title,
        work_type: workType,
        edition_label: "development",
      });
      say(`registered media work ${work.id}`);
      const asset = await api.addAsset(work.id, {
        local_path: path,
        provenance_class: provenance,
        rights_access_class: rights,
        retention_class: retention,
      });
      say(`asset ${asset.id} sha256=${asset.sha256.slice(0, 16)}…`);
      const run = await api.analyze(work.id, asset.id);
      say(`analysis run ${run.id} dispatched (${run.status})`);
      media.reload();
    } catch (e) {
      say(`error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="muted">
        Rights, provenance and retention are required fields. MP2 never infers them, and
        the API refuses any path outside the controlled ingest root.
      </p>
      <div className="form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Work type
          <input value={workType} onChange={(e) => setWorkType(e.target.value)} />
        </label>
        <label>
          Local path (inside ingest root)
          <input value={path} onChange={(e) => setPath(e.target.value)} />
        </label>
        <label>
          Provenance class
          <select value={provenance} onChange={(e) => setProvenance(e.target.value)}>
            {PROVENANCE_CLASSES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Rights / access class
          <select value={rights} onChange={(e) => setRights(e.target.value)}>
            {RIGHTS_CLASSES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Retention class
          <select value={retention} onChange={(e) => setRetention(e.target.value)}>
            {RETENTION_CLASSES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>
      <button className="primary" onClick={submit} disabled={busy}>
        {busy ? "Working…" : "Register and analyze"}
      </button>
      {log.length > 0 && (
        <pre className="log">{log.join("\n")}</pre>
      )}
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status.replace(/_/g, "-")}`}>{status}</span>;
}

function RunsPage({ onOpen }: { onOpen: (run: AnalysisRun) => void }) {
  const runs = useAsync(() => api.listRuns());
  if (runs.error) return <p className="error">{runs.error}</p>;
  if (!runs.data?.length) return <p className="muted">No analysis runs yet.</p>;

  return (
    <>
      <button onClick={runs.reload}>Refresh</button>
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Status</th>
            <th>Version</th>
            <th>Workflow</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.data.map((run) => (
            <tr key={run.id}>
              <td className="mono">{run.id.slice(0, 8)}…</td>
              <td>
                <StatusPill status={run.status} />
              </td>
              <td>v{run.version}</td>
              <td className="mono small">{run.workflow_id ?? "—"}</td>
              <td>
                <button onClick={() => onOpen(run)}>Evidence</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function EvidencePage({ runId }: { runId: string | null }) {
  const runs = useAsync(() => api.listRuns());
  const [selected, setSelected] = useState<string | null>(runId);
  const active = selected ?? runs.data?.[0]?.id ?? null;
  const evidence = useAsync<RunEvidence | null>(
    () => (active ? api.runEvidence(active) : Promise.resolve(null)),
    [active],
  );

  if (!runs.data?.length) return <p className="muted">No analysis runs yet.</p>;

  const data = evidence.data;
  return (
    <>
      <label className="inline">
        Analysis run
        <select value={active ?? ""} onChange={(e) => setSelected(e.target.value)}>
          {runs.data.map((run) => (
            <option key={run.id} value={run.id}>
              {run.id.slice(0, 8)}… ({run.status})
            </option>
          ))}
        </select>
      </label>

      {evidence.error && <p className="error">{evidence.error}</p>}
      {data && (
        <>
          <div className="grid">
            <div className="metric">
              <span>Segments</span>
              <strong>{data.segments.length}</strong>
            </div>
            <div className="metric">
              <span>Measurements</span>
              <strong>{data.measurements.length}</strong>
            </div>
            <div className="metric">
              <span>Evidence claims</span>
              <strong>{data.evidence_claims.length}</strong>
            </div>
            <div className="metric">
              <span>Model executions</span>
              <strong>{data.model_executions.length}</strong>
            </div>
          </div>

          <h3>Measurements</h3>
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Extractor</th>
                <th>Class</th>
                <th>Segment</th>
                <th>Content hash</th>
              </tr>
            </thead>
            <tbody>
              {data.measurements.map((m) => (
                <tr key={m.id}>
                  <td>{m.metric}</td>
                  <td className="small">
                    {m.extractor ? `${m.extractor.id}@${m.extractor.version}` : "—"}
                  </td>
                  <td>
                    <span className="pill">{m.extractor?.repeatability_class ?? "?"}</span>
                  </td>
                  <td className="mono small">
                    {m.segment_id ? `${m.segment_id.slice(0, 8)}…` : "work-level"}
                  </td>
                  <td className="mono small">{m.content_hash.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Evidence claims</h3>
          {data.evidence_claims.length === 0 ? (
            <p className="muted">
              No semantic claims recorded. Deterministic measurements above stand on their
              own; MP2 does not synthesise claims when no model produced them.
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Claim</th>
                  <th>Confidence</th>
                  <th>Segment</th>
                  <th>Evidence refs</th>
                </tr>
              </thead>
              <tbody>
                {data.evidence_claims.map((c) => (
                  <tr key={c.id}>
                    <td>{c.claim_type}</td>
                    <td>{c.confidence.toFixed(2)}</td>
                    <td className="mono small">
                      {c.segment_id ? `${c.segment_id.slice(0, 8)}…` : "—"}
                    </td>
                    <td className="small">{c.evidence_refs.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Model executions</h3>
          {data.model_executions.length === 0 ? (
            <p className="muted">No model executions for this run.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Adapter</th>
                  <th>Model</th>
                  <th>Location</th>
                  <th>Latency</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {data.model_executions.map((e) => (
                  <tr key={e.id}>
                    <td>{e.provider_adapter}</td>
                    <td className="small">
                      {e.model_tool_id}@{e.model_tool_version}
                    </td>
                    <td>
                      <span className="pill">{e.execution_location}</span>
                    </td>
                    <td>{e.latency_ms} ms</td>
                    <td>{e.estimated_cost}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}

function WorksPage() {
  const media = useAsync(() => api.listMedia());
  if (!media.data?.length) return <p className="muted">No media works registered.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Type</th>
          <th>Version</th>
          <th>ID</th>
        </tr>
      </thead>
      <tbody>
        {media.data.map((work: MediaWork) => (
          <tr key={work.id}>
            <td>{work.title}</td>
            <td>{work.work_type}</td>
            <td>v{work.version}</td>
            <td className="mono small">{work.id.slice(0, 8)}…</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("status");
  const [runId, setRunId] = useState<string | null>(null);

  const openEvidence = (run: AnalysisRun) => {
    setRunId(run.id);
    setPage("evidence");
  };

  return (
    <main>
      <header>
        <span className="mark">MP2</span>
        <div>
          <h1>Evidence console</h1>
          <p>Private · local-first · provider-neutral</p>
        </div>
      </header>

      <nav>
        {PAGES.map(([id, label]) => (
          <button
            key={id}
            className={page === id ? "active" : ""}
            onClick={() => setPage(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      <section>
        <p className="eyebrow">{page}</p>
        <h2>{PAGES.find((p) => p[0] === page)?.[1]}</h2>

        {page === "status" && <StatusPage />}
        {page === "ingest" && <IngestPage />}
        {page === "runs" && <RunsPage onOpen={openEvidence} />}
        {page === "evidence" && <EvidencePage runId={runId} />}
        {page === "works" && <WorksPage />}
        {SCAFFOLD_PAGES.includes(page) && (
          <p className="muted">
            This product route is intentionally a scaffold. Viewer modelling, state/context
            capture and recommendation logic depend on calibration work that is out of
            scope for M0–M4, and inventing a psychometric mapping here would be worse than
            leaving it empty.
          </p>
        )}
      </section>
    </main>
  );
}
