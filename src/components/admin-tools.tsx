"use client";

import { useState } from "react";
import { Check, FileUp, ShieldAlert } from "lucide-react";
import type { Column, DatasetOption, WorkspaceDataset } from "./workspace-types";

export function AdminTools({ organizationId, dataset, datasets, columns, onSelect, onUploaded }: {
  organizationId: string;
  dataset: WorkspaceDataset;
  datasets: DatasetOption[];
  columns: Column[];
  onSelect: (datasetId: string) => Promise<void>;
  onUploaded: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [bounds, setBounds] = useState("{}");
  const [categories,setCategories]=useState("{}");
  // Owner controls the ingestion API has accepted since migrations 006 and
  // 008. They had no form here, so the only way to set them was a direct API
  // call -- a data-owner control the data owner could not reach.
  const [entityColumn, setEntityColumn] = useState("");
  const [maxContributions, setMaxContributions] = useState(1);
  const [sensitiveColumns, setSensitiveColumns] = useState("[]");
  const [rowRestrictions, setRowRestrictions] = useState("[]");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setMessage("");
    try {
      JSON.parse(bounds);
      JSON.parse(categories);
      JSON.parse(sensitiveColumns);
      JSON.parse(rowRestrictions);
      const form = new FormData();
      form.append("file", file);
      form.append("name", name || file.name.replace(/\.csv$/i, ""));
      form.append("organizationId", organizationId);
      form.append("publicBounds", bounds);
      form.append("publicCategories",categories);
      // Empty entityColumn means the row stays the privacy unit, which is the
      // historical default -- send nothing rather than an empty string.
      if (entityColumn.trim()) {
        form.append("entityColumn", entityColumn.trim());
        form.append("maxContributions", String(maxContributions));
      }
      form.append("sensitiveColumns", sensitiveColumns);
      form.append("rowRestrictions", rowRestrictions);
      const response = await fetch("/api/datasets/upload", { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Upload failed.");
      setMessage(`Uploaded ${Number(body.rows).toLocaleString()} protected rows.`);
      setFile(null);
      setName("");
      setEntityColumn("");
      setMaxContributions(1);
      setSensitiveColumns("[]");
      setRowRestrictions("[]");
      await onUploaded();
    } catch (cause) {
      setMessage(cause instanceof SyntaxError ? "Bounds, categories, sensitive columns and row restrictions must all be valid JSON." : cause instanceof Error ? cause.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="view-stack stagger">
    <div className="view-title" style={{ "--i": 0 } as React.CSSProperties}><div><span className="kicker">§ 02 — Sources</span><h1>Datasets</h1><p>Inspect release permissions before asking a protected question.</p></div><span className="sync-state"><i /> {columns.length} fields declared</span></div>
    <section className="surface" aria-labelledby="dataset-list-title" style={{ "--i": 1 } as React.CSSProperties}>
      <div className="section-heading"><div><h2 id="dataset-list-title">Dataset inventory</h2><p>{datasets.length || 1} protected dataset{datasets.length === 1 ? "" : "s"} in this organization</p></div></div>
      <div className="dataset-list">{(datasets.length ? datasets : [{ id: dataset.id, name: dataset.name }]).map(item => {
        const active = item.id === dataset.id;
        return <button key={item.id} className={`dataset-row ${active ? "active" : ""}`} onClick={() => onSelect(item.id)} aria-pressed={active}>
          <span className="dataset-mark">{active ? <Check size={16} /> : null}</span>
          <span><strong>{item.name}</strong><small>{item.description || (active ? dataset.description : "Protected tabular dataset")}</small></span>
          <span className="dataset-meta tabular">{active ? `${dataset.rows.toLocaleString()} rows · ${dataset.columns} fields` : item.row_count != null ? `${item.row_count.toLocaleString()} rows · ${item.column_count ?? 0} fields` : item.status || "Protected"}</span>
        </button>;
      })}</div>
    </section>

    <section className="surface" aria-labelledby="schema-title" style={{ "--i": 2 } as React.CSSProperties}>
      <div className="section-heading"><div><h2 id="schema-title">Release schema</h2><p>Types, allowed operations, and public clipping bounds for {dataset.name}</p></div></div>
      <div className="table-scroll"><table><thead><tr><th>Field</th><th>Type</th><th>Allowed operations</th><th>Public bounds</th><th>Sensitive</th></tr></thead>
        <tbody>{columns.length ? columns.map(column => {
          // Sensitive columns may still be measured, but never grouped or
          // filtered on -- see migration-008.
          // Grouping additionally requires a declared public category domain:
          // without one the query route refuses the release, so "Group" here
          // would name an operation that always fails. Columns from a
          // response predating has_public_domain report undefined, so treat
          // the flag as satisfied rather than blanking their whole row.
          const groupable = column.can_group && !column.is_sensitive && (column.has_public_domain ?? true);
          const operations = [groupable && "Group / top-k / filter", column.can_measure && "Mean / sum / histogram"].filter(Boolean);
          // A sensitive column is barred from filtering too, so the old
          // "Filter only" fallback described the one thing it cannot do.
          const fallback = column.is_sensitive ? "Not releasable" : "Filter only";
          return <tr key={column.name}><td className="cell-title"><strong>{column.name}</strong></td><td className="cell-meta">{column.data_type}</td><td>{operations.length ? operations.join(", ") : fallback}</td><td className="tabular">{column.lower_bound == null || column.upper_bound == null ? "Not bounded" : `[${column.lower_bound}, ${column.upper_bound}]`}</td><td>{column.is_sensitive ? <span className="pill bad">Sensitive</span> : ""}</td></tr>;
        }) : <tr><td colSpan={5} className="empty-cell">No schema fields were returned for this dataset.</td></tr>}</tbody>
      </table></div>
      <div className="schema-note"><ShieldAlert size={16} /><span>Means and histograms are available only for numeric fields with public bounds. Bounds are safe to disclose and are applied before aggregation. Sensitive fields may be measured but are never grouped or filtered on.</span></div>
    </section>

    <section className="surface upload-surface" aria-labelledby="upload-title" style={{ "--i": 3 } as React.CSSProperties}>
      <div className="section-heading"><div><h2 id="upload-title">Add a protected dataset</h2><p>CSV and Parquet files are normalized and encrypted before private storage.</p></div><FileUp size={20} /></div>
      <div className="form-grid"><label>Dataset name<input value={name} onChange={event => setName(event.target.value)} placeholder="Quarterly care outcomes" /></label><label>CSV or Parquet file<input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" onChange={event => setFile(event.target.files?.[0] || null)} /></label></div>
      <label className="full-label">Public numeric bounds<textarea value={bounds} onChange={event => setBounds(event.target.value)} rows={4} spellCheck={false} placeholder={'{"age":[18,90],"score":[0,100]}'} /><span>JSON map from numeric field names to lower and upper clipping bounds.</span></label>
      <label className="full-label">Public category domains<textarea value={categories} onChange={event=>setCategories(event.target.value)} rows={4} spellCheck={false} placeholder={'{"region":["north","south","east","west"]}'} /><span>Grouped count and top-k only consider these owner-declared labels; private files do not define the released category domain.</span></label>
      <div className="form-grid">
        <label>Entity column<input value={entityColumn} onChange={event => setEntityColumn(event.target.value)} placeholder="patient_id" /><span>Leave blank if one row is one person. Naming a column makes it the privacy unit.</span></label>
        <label>Max rows per entity<input type="number" min={1} max={100} step={1} value={maxContributions} disabled={!entityColumn.trim()} onChange={event => {
          // Clearing the field or typing a non-digit made this NaN/0, and the
          // server only rejects it after the entire file has been uploaded --
          // a 19 MB round trip to learn about a one-character mistake. The
          // bound is [1, 100] to match the upload route's own validation.
          const next = Math.trunc(Number(event.target.value));
          setMaxContributions(Number.isFinite(next) && next >= 1 ? Math.min(next, 100) : 1);
        }} /><span>Rows beyond this are dropped before aggregation, and every sensitivity scales by it.</span></label>
      </div>
      <label className="full-label">Sensitive columns<textarea value={sensitiveColumns} onChange={event => setSensitiveColumns(event.target.value)} rows={2} spellCheck={false} placeholder={'["diagnosis","hiv_status"]'} /><span>JSON array of field names. A sensitive field can still be measured, but never grouped on or filtered by: a group label or filter predicate reveals the attribute directly however much noise the count carries.</span></label>
      <label className="full-label">Row restrictions<textarea value={rowRestrictions} onChange={event => setRowRestrictions(event.target.value)} rows={3} spellCheck={false} placeholder={'[{"column":"consent","operator":"equals","value":"true"}]'} /><span>Applied to every query before aggregation and never shown to analysts. Up to 10 entries. These narrow the rows; they do not change sensitivity or noise.</span></label>
      <div className="form-actions"><button className="button primary" disabled={!file || busy || !organizationId} onClick={upload}>{busy ? "Protecting dataset..." : "Upload and protect"}</button>{message && <p className={message.startsWith("Uploaded") ? "success-message" : "error-message"} role="status">{message}</p>}</div>
    </section>
  </div>;
}
