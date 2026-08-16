import { describe, expect, it } from "vitest";
import { auditableQueryMetadata } from "./audit";

describe("auditable query metadata", () => {
  const spec = {
    type: "grouped_count",
    epsilon: 0.5,
    groupBy: "care_program",
    valueColumn: undefined,
    filters: [
      { column: "diagnosis", operator: "equals", value: "HIV" },
      { column: "region", operator: "not_equals", value: "north" },
    ],
  };

  it("never records filter values", () => {
    const serialized = JSON.stringify(auditableQueryMetadata(spec));
    // The audit log is readable by every org member with view_audit_log. A
    // recorded predicate would disclose the attribute directly, whatever
    // noise was added to the released number.
    expect(serialized).not.toContain("HIV");
    expect(serialized).not.toContain("north");
  });

  it("keeps the columns a query touched", () => {
    const metadata = auditableQueryMetadata(spec);
    expect(metadata.filter_columns).toEqual(["diagnosis", "region"]);
    expect(metadata.filter_count).toBe(2);
    expect(metadata.group_by).toBe("care_program");
  });

  it("records the operation and its privacy cost", () => {
    const metadata = auditableQueryMetadata(spec);
    expect(metadata.query_type).toBe("grouped_count");
    expect(metadata.epsilon).toBe(0.5);
  });

  it("normalizes absent fields to null rather than dropping them", () => {
    const metadata = auditableQueryMetadata({ type: "count", epsilon: 0.1, filters: [] });
    expect(metadata.group_by).toBeNull();
    expect(metadata.value_column).toBeNull();
    expect(metadata.filter_columns).toEqual([]);
  });

  it("emits no key beyond the published allow-list", () => {
    // Pins the shape: a new field must be added deliberately, not inherited
    // from a spec object that happens to carry sensitive values.
    expect(Object.keys(auditableQueryMetadata(spec)).sort()).toEqual([
      "epsilon", "filter_columns", "filter_count", "group_by", "query_type", "value_column",
    ]);
  });
});
