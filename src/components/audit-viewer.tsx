"use client";
import { useCallback, useEffect, useState } from "react";

type AuditEvent = {
  id: string;
  event_type: string;
  resource_type: string;
  resource_id: string | null;
  actor_email: string | null;
  event_metadata: Record<string, unknown>;
  created_at: string;
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  "query.reserved": "Budget reserved",
  "query.released": "Answer released",
  "query.failed": "Release failed",
  "dataset.uploaded": "Dataset uploaded",
  "dataset.revoked": "Dataset revoked",
  "dataset.deleted": "Dataset deleted",
  "privacy_policy.updated": "Policy updated",
  "member.revoked": "Member revoked",
  "permission.granted": "Permission granted",
  "permission.revoked": "Permission revoked",
};

const EVENT_ICONS: Record<string, string> = {
  "query.reserved": "⏳",
  "query.released": "📊",
  "query.failed": "⚠️",
  "dataset.uploaded": "📁",
  "dataset.revoked": "🔒",
  "dataset.deleted": "🗑️",
  "privacy_policy.updated": "⚙️",
  "member.revoked": "👤",
  "permission.granted": "✅",
  "permission.revoked": "❌",
};

export default function AuditViewer({
  organizationId,
}: {
  organizationId: string;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [typeFilter, setTypeFilter] = useState("");
  const limit = 30;

  const fetchEvents = useCallback(
    async (currentOffset: number, append: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          organizationId,
          limit: String(limit),
          offset: String(currentOffset),
        });
        if (typeFilter) params.set("eventType", typeFilter);

        const response = await fetch(`/api/audit?${params}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to load audit events");

        setEvents((prev) => (append ? [...prev, ...data.events] : data.events));
        setHasMore(data.hasMore);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    },
    [organizationId, typeFilter]
  );

  useEffect(() => {
    setOffset(0);
    fetchEvents(0, false);
  }, [fetchEvents]);

  function loadMore() {
    const next = offset + limit;
    setOffset(next);
    fetchEvents(next, true);
  }

  function formatTimestamp(iso: string) {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatMetadata(meta: Record<string, unknown>) {
    const entries = Object.entries(meta).filter(
      ([, v]) => v !== null && v !== undefined && v !== ""
    );
    if (!entries.length) return "—";
    return entries.map(([k, v]) => `${k}: ${v}`).join(", ");
  }

  return (
    <div className="surface fade-up">
      <div className="section-heading">
        <div>
          <span className="kicker">§ 06 — Record</span>
          <h2>Audit log</h2>
          <p>All recorded actions for this organization. Query rows record the operation, the columns it touched, and the epsilon it spent — never the filter values, which would themselves be disclosive.</p>
        </div>
        <label className="filter-select">
          <span>Event type</span>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            aria-label="Filter by event type"
          >
            <option value="">All event types</option>
            <option value="query.released">Answer released</option>
            <option value="query.reserved">Budget reserved</option>
            <option value="query.failed">Release failed</option>
            <option value="dataset.uploaded">Dataset uploaded</option>
            <option value="dataset.revoked">Dataset revoked</option>
            <option value="dataset.deleted">Dataset deleted</option>
            <option value="privacy_policy.updated">Policy updated</option>
            <option value="member.revoked">Member revoked</option>
            <option value="permission.granted">Permission granted</option>
            <option value="permission.revoked">Permission revoked</option>
          </select>
        </label>
      </div>

      {error && <p className="error-message">{error}</p>}

      {events.length === 0 && !loading ? (
        <p className="empty-message">No audit events recorded yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th className="cell-glyph"></th>
                <th>Event</th>
                <th>Actor</th>
                <th>Resource</th>
                <th>Details</th>
                <th className="right">Time</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="cell-glyph" aria-hidden="true">
                    {EVENT_ICONS[event.event_type] || "📋"}
                  </td>
                  <td className="cell-title">
                    <strong>{EVENT_TYPE_LABELS[event.event_type] || event.event_type}</strong>
                  </td>
                  <td className="cell-meta">{event.actor_email || "—"}</td>
                  <td>
                    <span className="pill capitalize">{event.resource_type}</span>
                  </td>
                  <td
                    className="cell-meta cell-truncate"
                    title={formatMetadata(event.event_metadata)}
                  >
                    {formatMetadata(event.event_metadata)}
                  </td>
                  <td className="cell-time">{formatTimestamp(event.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {loading && <p className="loading-line">Loading audit events</p>}

      {hasMore && !loading && (
        <div className="load-more">
          <button className="button secondary" onClick={loadMore}>
            Load more
          </button>
        </div>
      )}
    </div>
  );
}
