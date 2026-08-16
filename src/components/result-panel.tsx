"use client";
/* global Blob, document, URL */

import { BarChart, Bar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download, ShieldCheck } from "lucide-react";
import type { QueryResult, QueryType } from "./workspace-types";

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function ResultPanel({ result, type }: { result: QueryResult; type: QueryType }) {
  const rows: Array<{ label: string; value: number; range?: number }> = result.values?.map(item => ({ label: item.group, value: item.value, range: item.range ?? result.range }))
    ?? result.buckets?.map(item => ({ label: `${item.from}-${item.to}`, value: item.value, range: item.range ?? result.range })) ?? [];
  // A range that omits a known error term must not be labelled as a
  // calibrated interval, or the analyst will read it as tighter than it is.
  const rangeCaveat = result.range_basis === "sum_noise_only"
    ? "Indicative scale of the sum noise only; noise on the count adds further error."
    : null;
  const csv = rows.length
    ? `label,value,uncertainty\n${rows.map(row => `${JSON.stringify(row.label)},${row.value},${row.range ?? ""}`).join("\n")}`
    : `metric,value,uncertainty\n${type},${result.value ?? ""},${result.range ?? ""}`;

  return <section className="result-panel fade-up" aria-labelledby="result-title">
    <div className="section-heading result-heading">
      <div>
        <div className="status-label"><ShieldCheck size={14} /> Protected release</div>
        <h2 id="result-title">Released answer</h2>
        <p>Noise is already applied. This is the only version of this figure that exists outside the protected environment.</p>
      </div>
      <div className="download-actions">
        <button className="button secondary" onClick={() => download("veil-result.json", JSON.stringify(result, null, 2), "application/json")}><Download size={15} /> JSON</button>
        <button className="button secondary" onClick={() => download("veil-result.csv", csv, "text/csv")}><Download size={15} /> CSV</button>
      </div>
    </div>

    {typeof result.value === "number" && <div className="single-result">
      <div className="result-number tabular">{result.value.toLocaleString()}</div>
      <div className="uncertainty">Estimated uncertainty <strong className="tabular">±{result.range ?? "not provided"}</strong></div>
      {rangeCaveat && <div className="bounds-note">{rangeCaveat}</div>}
      {result.bounds && <div className="bounds-note">Clipped to public bounds [{result.bounds.join(", ")}]</div>}
      {result.minimum_denominator != null && typeof result.range === "number" && result.bounds
        && result.range > (result.bounds[1] - result.bounds[0]) * 0.2
        && <div className="bounds-note danger">The noise here is a large fraction of the public bound range. Treat this value as uninformative.</div>}
    </div>}

    {!!rows.length && <>
      <div className="result-chart" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 12, right: 8, left: -14, bottom: 4 }}>
            <CartesianGrid stroke="#cfccbd" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#63736a", fontFamily: "var(--mono)" }} tickLine={false} axisLine={{ stroke: "#0c1410", strokeWidth: 2 }} />
            <YAxis tick={{ fontSize: 10, fill: "#63736a", fontFamily: "var(--mono)" }} tickLine={false} axisLine={false} />
            <Tooltip
              cursor={{ fill: "rgba(255,197,61,.28)" }}
              contentStyle={{ border: "2px solid #0c1410", borderRadius: 0, boxShadow: "4px 4px 0 #0c1410", fontFamily: "var(--mono)", fontSize: 11 }}
            />
            <Bar dataKey="value" fill="#0b5c3f" stroke="#0c1410" strokeWidth={2} isAnimationActive animationDuration={620} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="table-scroll">
        <table><thead><tr><th>{result.values ? "Group" : "Public interval"}</th><th>Protected value</th><th>Uncertainty</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.label}><td>{row.label}</td><td className="tabular">{row.value.toLocaleString()}</td><td className="tabular">±{row.range ?? "N/A"}</td></tr>)}</tbody>
        </table>
      </div>
    </>}

    <div className="release-notes">
      <div><span>Mechanism</span><strong>{result.mechanism ?? "Not reported"}</strong></div>
      <div><span>Limitations</span><strong>{result.note ?? "This answer includes calibrated noise and must not be treated as exact."}</strong></div>
      {result.selection && <div><span>Group selection</span><strong>{result.selection.replaceAll("_", " ")}{result.truncated ? "; output capped by policy" : ""}</strong></div>}
      {result.suppression_threshold != null && <div>
        <span>Suppression threshold</span>
        <strong>{result.suppression_threshold} noisy records{result.min_group_size != null ? ` (policy minimum ${result.min_group_size} plus the selection noise margin)` : ""}</strong>
      </div>}
      {result.minimum_denominator && <div><span>Mean denominator floor</span><strong>{result.minimum_denominator} records</strong></div>}
      {result.remaining_delta != null && <div><span>Delta remaining</span><strong>{result.remaining_delta.toExponential(1)} δ</strong></div>}
      {result.privacy_unit && <div><span>Privacy unit</span><strong>{result.privacy_unit}{result.max_contributions != null ? ` (up to ${result.max_contributions} row${result.max_contributions === 1 ? "" : "s"} each)` : ""}</strong></div>}
      {/* Count only, by design -- the restriction's column, operator, and
          value must never reach an analyst. See migration-008. */}
      {result.row_restrictions_applied != null && result.row_restrictions_applied > 0 && <div>
        <span>Row restrictions</span>
        <strong>{result.row_restrictions_applied} active restriction{result.row_restrictions_applied === 1 ? "" : "s"} applied before aggregation</strong>
      </div>}
    </div>
  </section>;
}
