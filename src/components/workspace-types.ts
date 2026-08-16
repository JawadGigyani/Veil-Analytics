import type { Dataset, LedgerEvent } from "@/lib/domain";

export type Column = {
  name: string;
  data_type: string;
  can_group: boolean;
  can_measure: boolean;
  lower_bound: number | null;
  upper_bound: number | null;
  /** May still be measured (mean/sum/histogram), but never used for group_by
   *  or a filter -- grouping or filtering on it would reveal the attribute
   *  directly. See migration-008 and src/lib/domain.ts. */
  is_sensitive: boolean;
  /** True when the owner declared a public category domain for this column.
   *  A grouped release is refused without one, so this -- not can_group,
   *  which only reflects the column's type -- decides whether grouping can
   *  actually succeed. Optional so a response predating this field is still
   *  a valid Column. */
  has_public_domain?: boolean;
};

export type DatasetOption = {
  id: string;
  name: string;
  description?: string;
  row_count?: number;
  column_count?: number;
  status?: string;
  updated_at?: string;
  file_format?: string;
};

export type Organization = { id: string; name: string; role: string };
export type WorkspaceDataset = Dataset & {
  minGroupSize: number;
  /** Denominator floor the bounded-mean mechanism divides by. Drives the
   *  uncertainty estimate shown before a release is paid for. */
  publicMinDenominator: number;
  deltaTotal: number;
  deltaUsed: number;
  allowedQueryTypes: QueryType[];
  /** Column identifying the privacy unit (e.g. person_id). Null means the row
   *  itself is the privacy unit -- today's unenforced default. */
  entityColumn: string | null;
  /** Per-entity row cap sensitivity is scaled by. 1 with entityColumn null
   *  reproduces one-row-per-person, unbounded-contribution behaviour. */
  maxContributions: number;
};
export type WorkspaceState = {
  dataset: WorkspaceDataset;
  datasets: DatasetOption[];
  columns: Column[];
  events: LedgerEvent[];
  organizationId: string;
  role: string;
  userEmail: string | null;
};

export type FilterSpec = { column: string; operator: "equals" | "not_equals"; value: string };
export type QueryType = "count" | "grouped_count" | "mean" | "bounded_sum" | "histogram" | "top_k";
export type QueryResult = {
  value?: number;
  range?: number;
  /** "sum_noise_only" marks a range that omits a known error term, so the UI
   *  can avoid presenting it as a calibrated confidence interval. */
  range_basis?: string;
  bounds?: [number, number];
  values?: Array<{ group: string; value: number; range?: number }>;
  buckets?: Array<{ from: number; to: number; value: number; range?: number }>;
  selection?: string;
  truncated?: boolean;
  suppression_threshold?: number;
  min_group_size?: number;
  minimum_denominator?: number;
  remaining_epsilon?: number;
  remaining_delta?: number;
  mechanism?: string;
  note?: string;
  estimated_counts?: Record<string, number>;
  /** Echoed back by the worker so the release notes show what privacy unit
   *  and contribution bound the noise was actually calibrated against. */
  privacy_unit?: string;
  max_contributions?: number;
  /** Count of owner-defined row restrictions the worker applied before
   *  aggregating, if it reports one. Never their column, operator, or
   *  value -- a restriction may itself encode a sensitive predicate. */
  row_restrictions_applied?: number;
};

export type AuditEvent = {
  id: string;
  event_type: string;
  resource_type: string;
  resource_id: string | null;
  actor_user_id: string | null;
  actor_email: string | null;
  event_metadata: Record<string, unknown>;
  created_at: string;
};

export type DatasetPermission = {
  dataset_id: string;
  user_id: string;
  permission: string;
  user_email: string | null;
  granted_by_email: string | null;
  created_at: string;
};
