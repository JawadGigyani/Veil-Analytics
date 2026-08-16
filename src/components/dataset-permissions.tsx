"use client";
import { useCallback, useEffect, useState } from "react";

type DatasetPermission = {
  dataset_id: string;
  user_id: string;
  permission: string;
  user_email: string | null;
  granted_by_email: string | null;
  created_at: string;
};

const PERMISSION_LABELS: Record<string, string> = {
  view_schema: "View schema",
  run_queries: "Run queries",
  manage_dataset: "Manage dataset",
  view_audit_log: "View audit log",
};

export default function DatasetPermissions({
  datasetId,
}: {
  datasetId: string;
}) {
  const [permissions, setPermissions] = useState<DatasetPermission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [userId, setUserId] = useState("");
  const [newPermission, setNewPermission] = useState("run_queries");
  const [submitting, setSubmitting] = useState(false);

  const fetchPermissions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/datasets/${datasetId}/permissions`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Failed to load");
      setPermissions(data.permissions || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    fetchPermissions();
  }, [fetchPermissions]);

  async function grantPermission(e: React.FormEvent) {
    e.preventDefault();
    if (!userId.trim()) return;
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await fetch(`/api/datasets/${datasetId}/permissions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: userId.trim(), permission: newPermission }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Failed to grant permission");
      setSuccess("Permission granted successfully.");
      setUserId("");
      fetchPermissions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to grant");
    } finally {
      setSubmitting(false);
    }
  }

  async function revokePermission(targetUserId: string, permission: string) {
    setError(null);
    setSuccess(null);
    try {
      const response = await fetch(`/api/datasets/${datasetId}/permissions`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: targetUserId, permission }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Failed to revoke");
      setSuccess("Permission revoked.");
      fetchPermissions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  }

  return (
    <div className="surface fade-up">
      <div className="section-heading">
        <div>
          <h2>Dataset permissions</h2>
          <p>
            Manage per-user access for this dataset. Organization membership is still required.
          </p>
        </div>
      </div>

      {error && <p className="error-message">{error}</p>}
      {success && <p className="success-message">{success}</p>}

      <form onSubmit={grantPermission} className="invite-form grant-form">
        <label>User ID<input
          type="text"
          placeholder="User ID"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
        /></label>
        <label>Permission<select
          value={newPermission}
          onChange={(e) => setNewPermission(e.target.value)}
        >
          {Object.entries(PERMISSION_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select></label>
        <button
          type="submit"
          className="button primary"
          disabled={submitting || !userId.trim()}
        >
          {submitting ? "Granting…" : "Grant"}
        </button>
      </form>

      {loading ? (
        <p className="loading-line">Loading permissions</p>
      ) : permissions.length === 0 ? (
        <p className="empty-message">No dataset-level permissions configured.</p>
      ) : (
        <div className="member-list">
          {permissions.map((perm) => (
            <div
              key={`${perm.user_id}-${perm.permission}`}
              className="member-row"
            >
              <div className="member-identity">
                <span>
                  {(perm.user_email || perm.user_id.slice(0, 2)).toUpperCase().slice(0, 2)}
                </span>
                <div>
                  <strong>{perm.user_email || perm.user_id.slice(0, 8)}</strong>
                  <small>{PERMISSION_LABELS[perm.permission] || perm.permission}</small>
                </div>
              </div>
              <div className="member-actions">
                <span className="cell-meta">
                  {perm.granted_by_email ? `by ${perm.granted_by_email}` : ""}
                </span>
                <button
                  className="icon-button danger-button"
                  title="Revoke permission"
                  aria-label={`Revoke ${PERMISSION_LABELS[perm.permission] || perm.permission} for ${perm.user_email || perm.user_id.slice(0, 8)}`}
                  onClick={() => revokePermission(perm.user_id, perm.permission)}
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
