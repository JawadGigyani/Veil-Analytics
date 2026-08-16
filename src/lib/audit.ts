/**
 * What may be written to the organization-visible audit log.
 *
 * Audit rows are readable by every org member holding `view_audit_log`, so
 * anything recorded here is effectively published to the organization. The
 * audit trail must therefore describe *that* a release happened and what it
 * cost, without restating the sensitive predicate it was conditioned on.
 */

export type AuditableSpec = {
  type: string;
  epsilon: number;
  groupBy?: string;
  valueColumn?: string;
  filters: Array<{ column: string; operator?: string; value?: string }>;
};

/**
 * Reduce a query spec to the fields that are safe to publish.
 *
 * Filter *values* are dropped. A filter such as `diagnosis equals "HIV"` is
 * itself disclosive: recording it would let anyone with audit access read a
 * sensitive attribute straight out of the log, no matter how much noise was
 * added to the released number. Column names are kept because they are
 * already visible to anyone with `view_schema`.
 */
export function auditableQueryMetadata(spec: AuditableSpec): Record<string, unknown> {
  return {
    query_type: spec.type,
    epsilon: spec.epsilon,
    group_by: spec.groupBy ?? null,
    value_column: spec.valueColumn ?? null,
    filter_columns: spec.filters.map((filter) => filter.column),
    filter_count: spec.filters.length,
  };
}
