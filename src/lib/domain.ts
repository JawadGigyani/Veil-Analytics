import { z } from "zod";

export const filterSchema = z.object({ column: z.string().min(1), operator: z.enum(["equals", "not_equals"]), value: z.string().min(1).max(80) });

/**
 * Owner-defined row restrictions (original plan section 2). Same shape as an
 * analyst filter -- {column, operator, value} with operator restricted to
 * equals/not_equals -- but they are configuration set at ingestion, AND-ed
 * into every query by the worker before aggregation. At most 10, matching
 * the CHECK constraint in migration-008. This schema validates *shape* only:
 * whether the column names actually exist is checked against the
 * worker-returned schema at ingestion, and the *content* of a restriction
 * must never be echoed back to an analyst, an error message, or the audit
 * log -- it may itself encode a sensitive predicate.
 */
export const rowRestrictionsSchema = z.array(filterSchema).max(10);

export const querySchema = z.object({
  datasetId: z.string().uuid().optional(),
  type: z.enum(["count", "grouped_count", "mean", "bounded_sum", "histogram", "top_k"]),
  groupBy: z.string().optional(), valueColumn: z.string().optional(), filters: z.array(filterSchema).max(3).default([]),
  // Messages are attached per-constraint because the query route surfaces the
  // first issue's message verbatim; Zod's defaults ("Too big: expected number
  // to be <=2") name no units and do not say what the number means.
  epsilon: z.number({ message: "Privacy cost must be a number between 0.1 and 2." }).finite("Privacy cost must be a finite number.").min(.1, "Privacy cost must be at least 0.1 epsilon.").max(2, "Privacy cost cannot exceed 2 epsilon in a single release."),
  bins: z.number().int().min(2, "Choose at least 2 bins.").max(20, "Choose at most 20 bins.").default(6),
  topK: z.number().int().min(1, "Top-k must return at least 1 group.").max(20, "Top-k cannot exceed 20 groups.").default(5),
  mechanism: z.enum(["laplace", "gaussian"]).default("laplace"),
  delta: z.number().finite().min(0).max(0.1).default(0),
}).superRefine((query, ctx) => {
  if ((query.type === "grouped_count" || query.type === "top_k") && !query.groupBy) ctx.addIssue({ code:"custom", path:["groupBy"], message:"A grouping column is required." });
  if ((query.type === "mean" || query.type === "bounded_sum" || query.type === "histogram") && !query.valueColumn) ctx.addIssue({ code:"custom", path:["valueColumn"], message:"A numeric value column is required." });
  // Gaussian and Laplace are calibrated to different (epsilon, delta)
  // guarantees, so each mechanism has a hard requirement on delta rather
  // than a shared range: Gaussian without delta > 0 is not a valid Gaussian
  // mechanism, and Laplace spending delta would silently claim a stronger
  // guarantee than a pure-epsilon mechanism provides.
  if (query.mechanism === "gaussian" && query.delta <= 0) ctx.addIssue({ code:"custom", path:["delta"], message:"Gaussian releases require a delta greater than 0." });
  if (query.mechanism === "laplace" && query.delta !== 0) ctx.addIssue({ code:"custom", path:["delta"], message:"Laplace releases must not spend delta." });
  // The bounded mean already splits epsilon across a noisy sum and a noisy
  // count; splitting delta across the same two releases too is an
  // unjustified design decision, not a supported combination.
  if (query.mechanism === "gaussian" && query.type === "mean") ctx.addIssue({ code:"custom", path:["mechanism"], message:"The bounded mean does not support the Gaussian mechanism." });
});
export type QuerySpec = z.infer<typeof querySchema>;

/**
 * True (and returns the offending column) if a query's groupBy or any
 * filter references a column marked `is_sensitive`. A sensitive column may
 * still be measured (mean/sum/histogram release a noisy aggregate) -- only
 * grouping and filtering are disallowed, because a group label or a filter
 * predicate reveals the attribute directly regardless of how much noise is
 * added to the count alongside it.
 *
 * Pure and column-name-only: the caller supplies which names are sensitive
 * from real dataset metadata (`src/app/api/query/route.ts`), so this can be
 * unit-tested without a database. Returning the column name is safe here --
 * column names are already visible to anyone with `view_schema`, unlike
 * filter *values*, which this function never touches or returns.
 */
export function usesSensitiveColumnForGroupingOrFiltering(
  spec: Pick<QuerySpec, "groupBy" | "filters">,
  sensitiveColumns: ReadonlySet<string> | readonly string[],
): string | null {
  const sensitive = sensitiveColumns instanceof Set ? sensitiveColumns : new Set(sensitiveColumns);
  if (spec.groupBy && sensitive.has(spec.groupBy)) return spec.groupBy;
  const hit = spec.filters.find((filter) => sensitive.has(filter.column));
  return hit ? hit.column : null;
}

export type Dataset = { id:string; name:string; description:string; rows:number; columns:number; epsilonTotal:number; epsilonUsed:number; updated:string; status:"Protected"|"Processing" };
export type LedgerEvent = { id:string; operation:string; dataset:string; epsilon:number; result:string; actor:string; time:string };
