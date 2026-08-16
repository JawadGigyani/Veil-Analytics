"use client";
import { useCallback, useEffect, useState } from "react";

type QueryRecord = {
  id: string;
  submitted_by: string | null;
  query_spec: {
    type?: string;
    epsilon?: number;
    groupBy?: string;
    valueColumn?: string;
    filters?: Array<{ column: string }>;
  } | null;
  status: string;
  epsilon_spent: number;
  released_result: Record<string, unknown> | null;
  error_message: string | null;
  finalized_at: string | null;
  created_at: string;
  normalized_query_hash: string | null;
};

const STATUS_LABELS: Record<string, string> = {
  reserved: "Reserved",
  completed: "Released",
  failed: "Failed",
};

export default function QueryHistory({
  datasetId,
  datasetName,
  role,
}: {
  datasetId: string;
  datasetName: string;
  role: string;
}) {
  const [queries, setQueries] = useState<QueryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const limit = 25;

  const fetchQueries = useCallback(
    async (currentOffset: number, append: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          limit: String(limit),
          offset: String(currentOffset),
        });
        if (statusFilter) params.set("status", statusFilter);

        const response = await fetch(`/api/datasets/${datasetId}/queries?${params}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to load query history");

        setQueries((prev) => (append ? [...prev, ...data.queries] : data.queries));
        setHasMore(data.hasMore);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    },
    [datasetId, statusFilter],
  );

  useEffect(() => {
    setOffset(0);
    fetchQueries(0, false);
  }, [fetchQueries]);

  function loadMore() {
    const next = offset + limit;
    setOffset(next);
    fetchQueries(next, true);
  }

  function formatTimestamp(iso: string) {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  }

  /** Describe the operation without restating the filter values it used. */
  function describe(record: QueryRecord) {
    const spec = record.query_spec || {};
    const operation = (spec.type || "query").replaceAll("_", " ");
    const target = spec.groupBy || spec.valueColumn;
    const filters = spec.filters?.length
      ? ` · ${spec.filters.length} filter${spec.filters.length > 1 ? "s" : ""}`
      : "";
    return `${operation}${target ? ` · ${target}` : ""}${filters}`;
  }

  /** Failed releases still consumed budget; make that visible, not implicit. */
  const spentOnFailures = queries
    .filter((record) => record.status === "failed")
    .reduce((total, record) => total + Number(record.epsilon_spent), 0);

  return (
    <div className="surface fade-up">
      <div className="section-heading">
        <div>
          <span className="kicker">§ 04 — History</span>
          <h2>Release history</h2>
          <p>
            {role === "analyst"
              ? `Your releases against ${datasetName}.`
              : `All releases against ${datasetName} by every member.`}
            {" "}Budget is reserved before execution and is not refunded, so a failed release still shows its cost.
          </p>
        </div>
        <label className="filter-select">
          <span>Status</span>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            <option value="completed">Released</option>
            <option value="reserved">Reserved</option>
            <option value="failed">Failed</option>
          </select>
        </label>
      </div>

      {error && <p className="error-message">{error}</p>}

      {spentOnFailures > 0 && (
        <p className="empty-message" role="status">
          {spentOnFailures.toFixed(1)} ε of the budget shown here was spent on releases that failed after reservation.
        </p>
      )}

      {queries.length === 0 && !loading ? (
        <p className="empty-message">No releases recorded for this dataset yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Operation</th>
                <th>Status</th>
                <th className="right">Cost</th>
                <th>Reference</th>
                <th className="right">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {queries.map((record) => (
                <tr key={record.id}>
                  <td className="cell-title">
                    <strong className="capitalize">{describe(record)}</strong>
                    {record.error_message && (
                      <div className="cell-error">{record.error_message}</div>
                    )}
                  </td>
                  <td>
                    <span className={`pill ${record.status === "failed" ? "bad" : record.status === "reserved" ? "wait" : "ok"}`}>
                      {STATUS_LABELS[record.status] || record.status}
                    </span>
                  </td>
                  <td className="cell-num">{Number(record.epsilon_spent).toFixed(1)} ε</td>
                  <td className="cell-meta">{record.normalized_query_hash || record.id.slice(0, 8)}</td>
                  <td className="cell-time">{formatTimestamp(record.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {loading && <p className="loading-line">Loading release history</p>}

      {hasMore && !loading && (
        <div className="load-more">
          <button className="button secondary" onClick={loadMore}>Load more</button>
        </div>
      )}
    </div>
  );
}
