"use client";

import { useState } from "react";
import { Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { useDialogFocus } from "./use-dialog-focus";
import {
  countConfidenceRadius,
  groupSuppressionThreshold,
  meanConfidenceRadius,
  meanIsUninformative,
} from "@/lib/uncertainty";
import type { Column, FilterSpec, QueryResult, QueryType, WorkspaceDataset } from "./workspace-types";

const operations: Array<{ value: QueryType; label: string }> = [
  { value: "count", label: "Count records" },
  { value: "grouped_count", label: "Grouped count" },
  { value: "mean", label: "Bounded mean" },
  { value: "bounded_sum", label: "Bounded sum" },
  { value: "histogram", label: "Histogram" },
  { value: "top_k", label: "Noisy top categories" },
];

export function QueryComposer({ open, onClose, dataset, columns, organizationId, onReleased }: {
  open: boolean; onClose: () => void; dataset: WorkspaceDataset; columns: Column[]; organizationId: string;
  onReleased: (result: QueryResult, type: QueryType) => Promise<void>;
}) {
  const [type, setType] = useState<QueryType>("grouped_count");
  const [groupBy, setGroupBy] = useState("");
  const [valueColumn, setValueColumn] = useState("");
  const [epsilon, setEpsilon] = useState(0.4);
  const [mechanism, setMechanism] = useState<"laplace" | "gaussian">("laplace");
  const [delta, setDelta] = useState(0.00001);
  const [bins, setBins] = useState(6);
  const [topK, setTopK] = useState(5);
  const [filters, setFilters] = useState<FilterSpec[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useDialogFocus(open, onClose);
  if (!open) return null;

  // The bounded mean already splits epsilon across a noisy sum and a noisy
  // count; splitting delta across the same two releases too is an
  // unjustified design decision the worker refuses, so Gaussian is not an
  // option for this operation.
  const gaussianAvailable = type !== "mean";
  const effectiveMechanism = gaussianAvailable ? mechanism : "laplace";
  // Sensitive columns may still be measured below, but a group label or a
  // filter predicate on one reveals the attribute directly no matter how
  // much noise is added to the aggregate released alongside it, so both
  // pickers exclude them. See migration-008 and src/lib/domain.ts.
  // has_public_domain is the gate the server actually enforces: without a
  // declared category domain a grouped release is refused outright. Filtering
  // on can_group alone offered identifier columns (patient_nbr,
  // encounter_id) that could never succeed and would have asked for one group
  // per person. Datasets served before this field existed report undefined,
  // so fall back to can_group rather than emptying their picker.
  const groupColumns = columns.filter(column =>
    column.can_group
    && !column.is_sensitive
    && (column.has_public_domain ?? true));
  const filterableColumns = columns.filter(column => !column.is_sensitive);
  const hasSensitiveColumns = columns.some(column => column.is_sensitive);
  const measureColumns = columns.filter(column => column.can_measure && column.lower_bound != null && column.upper_bound != null);
  const selectedMeasure = measureColumns.find(column => column.name === valueColumn) ?? measureColumns[0];
  const remaining = Math.max(0, dataset.epsilonTotal - dataset.epsilonUsed);
  const projected = remaining - epsilon;
  const remainingDelta = Math.max(0, dataset.deltaTotal - dataset.deltaUsed);
  const deltaCost = effectiveMechanism === "gaussian" ? delta : 0;
  const projectedDelta = remainingDelta - deltaCost;
  const countRange = Math.ceil(countConfidenceRadius(epsilon, dataset.maxContributions));

  const measureSpread = selectedMeasure
    ? Number(selectedMeasure.upper_bound) - Number(selectedMeasure.lower_bound)
    : null;
  const meanRange = measureSpread != null && measureSpread > 0
    ? meanConfidenceRadius({
        spread: measureSpread,
        epsilon,
        rows: dataset.rows,
        minDenominator: dataset.publicMinDenominator,
        maxContributions: dataset.maxContributions,
      })
    : null;
  const estimatedRange = type === "mean" && meanRange != null ? Math.max(0.1, meanRange).toFixed(1) : countRange;

  // A mean whose noise is a large fraction of the bound range carries no
  // information. Say so before the analyst pays for it -- budget is not
  // refunded once a release is reserved.
  const uninformativeMean = type === "mean"
    && meanRange != null && measureSpread != null
    && meanIsUninformative(meanRange, measureSpread);
  const groupedThreshold = ["grouped_count", "top_k"].includes(type)
    ? groupSuppressionThreshold(dataset.minGroupSize, epsilon, dataset.maxContributions)
    : null;
  const selectionValid = !["grouped_count", "top_k"].includes(type) || Boolean(groupBy || groupColumns[0]);
  const measureValid = !["mean", "bounded_sum", "histogram"].includes(type) || Boolean(selectedMeasure);
  const availableOperations = operations.filter(operation => dataset.allowedQueryTypes.includes(operation.value));

  function updateFilter(index: number, patch: Partial<FilterSpec>) {
    setFilters(current => current.map((filter, filterIndex) => filterIndex === index ? { ...filter, ...patch } : filter));
  }

  async function release() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-organization-id": organizationId },
        body: JSON.stringify({
          datasetId: dataset.id,
          type,
          groupBy: ["grouped_count", "top_k"].includes(type) ? (groupBy || groupColumns[0]?.name) : undefined,
          valueColumn: ["mean", "bounded_sum", "histogram"].includes(type) ? selectedMeasure?.name : undefined,
          epsilon,
          mechanism: effectiveMechanism,
          delta: deltaCost,
          bins,
          topK,
          filters,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "The protected answer could not be released.");
      await onReleased(payload.result, type);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The protected answer could not be released.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="query-title" className="dialog query-dialog">
      <div className="dialog-header"><div><div className="status-label"><ShieldCheck size={14} /> Protected query</div><h2 id="query-title">Compose a release</h2><p>Exact aggregates remain inside the protected environment. Only the noisy answer crosses the boundary.</p></div><button className="icon-button" onClick={onClose} aria-label="Close query composer"><X size={20} /></button></div>
      <div className="composer-grid">
        <div className="composer-fields">
          <label>Operation<select value={type} onChange={event => { setType(event.target.value as QueryType); setError(""); }}>{availableOperations.map(operation => <option key={operation.value} value={operation.value}>{operation.label}</option>)}</select></label>
          <label>Mechanism<select value={effectiveMechanism} disabled={!gaussianAvailable} onChange={event => setMechanism(event.target.value as "laplace" | "gaussian")}><option value="laplace">Laplace</option><option value="gaussian" disabled={!gaussianAvailable}>Gaussian</option></select></label>
          {!gaussianAvailable && <p className="muted-message">Gaussian is not available for the bounded mean: it already splits epsilon across a noisy sum and count, and splitting delta too is not a supported combination. Choose Laplace, or a different operation.</p>}
          {(type === "grouped_count" || type === "top_k") && <label>Group by<select value={groupBy} onChange={event => setGroupBy(event.target.value)}><option value="">Select a field</option>{groupColumns.map(column => <option key={column.name}>{column.name}</option>)}</select></label>}
          {(type === "mean" || type === "bounded_sum" || type === "histogram") && <label>Measured field<select value={valueColumn} onChange={event => setValueColumn(event.target.value)}><option value="">Select a bounded field</option>{measureColumns.map(column => <option key={column.name}>{column.name}</option>)}</select></label>}
          {type === "histogram" && <label>Histogram bins<div className="range-row"><input type="range" min="2" max="20" value={bins} onChange={event => setBins(Number(event.target.value))} /><output>{bins}</output></div></label>}
          {type === "top_k" && <label>Categories to release<div className="range-row"><input type="range" min="1" max="20" value={topK} onChange={event => setTopK(Number(event.target.value))} /><output>{topK}</output></div></label>}
          <fieldset className="filter-fieldset"><legend>Filters <span>{filters.length}/3</span></legend>
            {filters.map((filter, index) => <div className="filter-row" key={index}>
              <select aria-label={`Filter ${index + 1} field`} value={filter.column} onChange={event => updateFilter(index, { column: event.target.value })}>{filterableColumns.map(column => <option key={column.name}>{column.name}</option>)}</select>
              <select aria-label={`Filter ${index + 1} operator`} value={filter.operator} onChange={event => updateFilter(index, { operator: event.target.value as FilterSpec["operator"] })}><option value="equals">equals</option><option value="not_equals">does not equal</option></select>
              <input aria-label={`Filter ${index + 1} value`} value={filter.value} onChange={event => updateFilter(index, { value: event.target.value })} placeholder="Value" />
              <button className="icon-button" onClick={() => setFilters(current => current.filter((_, filterIndex) => filterIndex !== index))} aria-label={`Remove filter ${index + 1}`}><Trash2 size={16} /></button>
            </div>)}
            {filters.length < 3 && <button className="text-button" disabled={!filterableColumns.length} onClick={() => setFilters(current => [...current, { column: filterableColumns[0]?.name ?? "", operator: "equals", value: "" }])}><Plus size={15} /> Add filter</button>}
            {hasSensitiveColumns && <p className="muted-message">Sensitive fields are left out of grouping and filtering: a group label or filter predicate would reveal the attribute directly. They can still be measured (mean, sum, histogram).</p>}
          </fieldset>
        </div>
        <aside className="cost-preview" aria-label="Privacy release preview">
          <h3>Release preview</h3>
          {/* Cost, uncertainty, and the unit the noise protects -- the three
              things an analyst must see before budget is irreversibly spent. */}
          <div className="uncertainty-preview">
            <span>Privacy unit</span>
            <strong className="tabular">{dataset.entityColumn ? dataset.entityColumn : "row"}</strong>
            <p>{dataset.entityColumn
              ? `Each ${dataset.entityColumn} may contribute up to ${dataset.maxContributions} row${dataset.maxContributions === 1 ? "" : "s"}; noise is calibrated to that bound, not to one row.`
              : "One row is treated as one person. If this dataset has repeat rows per person, noise here understates the real uncertainty."}</p>
          </div>
          <label>Epsilon cost <strong className="tabular">{epsilon.toFixed(1)} ε</strong><input type="range" min="0.1" max="2" step="0.1" value={epsilon} onChange={event => setEpsilon(Number(event.target.value))} /></label>
          <div className="preview-ledger"><div><span>Available now</span><strong>{remaining.toFixed(1)} ε</strong></div><div><span>This release</span><strong>-{epsilon.toFixed(1)} ε</strong></div><div className={projected < 0 ? "danger" : ""}><span>After release</span><strong>{projected.toFixed(1)} ε</strong></div></div>
          {effectiveMechanism === "gaussian" && <label>Delta cost <strong className="tabular">{delta.toExponential(1)} δ</strong><input type="number" min="0.000001" max="0.1" step="0.000001" value={delta} onChange={event => setDelta(Number(event.target.value))} /></label>}
          <div className="preview-ledger"><div><span>Delta available</span><strong>{remainingDelta.toExponential(1)} δ</strong></div><div><span>This release</span><strong>-{deltaCost.toExponential(1)} δ</strong></div><div className={projectedDelta < 0 ? "danger" : ""}><span>After release</span><strong>{projectedDelta.toExponential(1)} δ</strong></div></div>
          <div className="uncertainty-preview">
            <span>Estimated uncertainty</span>
            <strong className="tabular">±{estimatedRange}</strong>
            <p>{type === "mean"
              ? "Indicative scale of the sum noise only. The independent noise on the count adds further error. Actual uncertainty is returned with the release."
              : "Actual uncertainty depends on the protected population and is returned with the release."}</p>
          </div>
          {groupedThreshold != null && <div className="uncertainty-preview">
            <span>Groups released</span>
            <strong className="tabular">≈{groupedThreshold.toFixed(0)}+ records</strong>
            <p>A group is released only when its noisy count clears the policy minimum of {dataset.minGroupSize} by the selection noise margin, so the floor holds with about 95% confidence.</p>
          </div>}
        </aside>
      </div>
      {error && <p className="error-message" role="alert">{error}</p>}
      {uninformativeMean && <p className="error-message" role="status">
        At {epsilon.toFixed(1)} ε this mean carries noise of roughly ±{estimatedRange} against a bound range of {measureSpread}. The released number will not be informative. Raise epsilon, narrow the public bounds, or use a count instead — budget is not refunded once a release is reserved.
      </p>}
      {!measureValid && <p className="error-message" role="status">This dataset has no numeric fields with public clipping bounds.</p>}
      {effectiveMechanism === "gaussian" && deltaCost <= 0 && <p className="error-message" role="status">Gaussian releases require a delta greater than 0.</p>}
      <div className="dialog-footer"><button className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy || projected < 0 || projectedDelta < 0 || !selectionValid || !measureValid || (effectiveMechanism === "gaussian" && deltaCost <= 0) || filters.some(filter => !filter.value)} onClick={release}>{busy ? "Reserving budget..." : `Release for ${epsilon.toFixed(1)} ε${effectiveMechanism === "gaussian" ? ` + ${deltaCost.toExponential(1)} δ` : ""}`}</button></div>
    </div>
  </div>;
}
