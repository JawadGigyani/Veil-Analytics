import { describe, expect, it } from "vitest";
import { filterSchema, querySchema, rowRestrictionsSchema, usesSensitiveColumnForGroupingOrFiltering } from "./domain";

describe("privacy domain", () => {
  it("validates only bounded query costs", () => {
    expect(querySchema.safeParse({ type: "count", epsilon: .4 }).success).toBe(true);
    expect(querySchema.safeParse({ type: "count", epsilon: 4 }).success).toBe(false);
  });

  it("requires fields for semantic operations", () => {
    expect(querySchema.safeParse({ type: "grouped_count", epsilon: .4 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "mean", epsilon: .4 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "mean", valueColumn: "age", epsilon: .4 }).success).toBe(true);
  });

  it("supports bounded sum and top-k with required fields", () => {
    expect(querySchema.safeParse({ type: "bounded_sum", epsilon: .4, valueColumn: "age" }).success).toBe(true);
    expect(querySchema.safeParse({ type: "top_k", epsilon: .4 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "top_k", epsilon: .4, groupBy: "region", topK: 3 }).success).toBe(true);
  });

  it("limits filters to safe operators", () => {
    expect(filterSchema.safeParse({ column: "region", operator: "equals", value: "north" }).success).toBe(true);
    expect(filterSchema.safeParse({ column: "region", operator: "contains", value: "north" }).success).toBe(false);
  });

  it("enforces the gaussian/laplace delta pairing", () => {
    // Gaussian requires a positive delta; laplace must spend none; the
    // bounded mean cannot use gaussian because it already splits epsilon
    // across a noisy sum and count, and splitting delta too is unsupported.
    expect(querySchema.safeParse({ type: "count", epsilon: .4, mechanism: "gaussian", delta: 0 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "count", epsilon: .4, mechanism: "gaussian", delta: 1e-5 }).success).toBe(true);
    expect(querySchema.safeParse({ type: "count", epsilon: .4, mechanism: "laplace", delta: 1e-5 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "count", epsilon: .4, mechanism: "laplace" }).success).toBe(true);
    expect(querySchema.safeParse({ type: "mean", valueColumn: "age", epsilon: .4, mechanism: "gaussian", delta: 1e-5 }).success).toBe(false);
  });

  it("rejects a query type outside the supported set", () => {
    expect(querySchema.safeParse({ type: "randomized_response", epsilon: .4 }).success).toBe(false);
    expect(querySchema.safeParse({ type: "raw_rows", epsilon: .4 }).success).toBe(false);
  });

  describe("sensitive column enforcement", () => {
    // A sensitive column may still be measured -- only grouping and
    // filtering are disallowed, because a group label or filter predicate
    // reveals the attribute directly no matter how much noise is added to
    // the aggregate released alongside it.
    it("rejects a sensitive column used as groupBy", () => {
      const hit = usesSensitiveColumnForGroupingOrFiltering({ groupBy: "diagnosis", filters: [] }, ["diagnosis"]);
      expect(hit).toBe("diagnosis");
    });

    it("rejects a sensitive column used as a filter", () => {
      const hit = usesSensitiveColumnForGroupingOrFiltering(
        { groupBy: undefined, filters: [{ column: "diagnosis", operator: "equals", value: "x" }] },
        ["diagnosis"],
      );
      expect(hit).toBe("diagnosis");
    });

    it("accepts a sensitive column when it is not used for grouping or filtering", () => {
      // A sensitive column referenced only as the measured valueColumn is
      // fine -- this function only inspects groupBy and filters, so a query
      // that measures "diagnosis" without grouping or filtering on it
      // passes clean.
      const hit = usesSensitiveColumnForGroupingOrFiltering({ groupBy: "region", filters: [] }, ["diagnosis"]);
      expect(hit).toBeNull();
    });

    it("passes clean when no sensitive column is referenced at all", () => {
      const hit = usesSensitiveColumnForGroupingOrFiltering(
        { groupBy: "region", filters: [{ column: "age_band", operator: "not_equals", value: "18-25" }] },
        [],
      );
      expect(hit).toBeNull();
    });
  });

  describe("row restriction shape validation", () => {
    it("accepts a valid restriction list", () => {
      expect(rowRestrictionsSchema.safeParse([{ column: "consent", operator: "equals", value: "true" }]).success).toBe(true);
      expect(rowRestrictionsSchema.safeParse([]).success).toBe(true);
    });

    it("rejects an operator outside equals/not_equals", () => {
      expect(rowRestrictionsSchema.safeParse([{ column: "consent", operator: "contains", value: "true" }]).success).toBe(false);
    });

    it("rejects more than 10 restrictions", () => {
      const many = Array.from({ length: 11 }, (_, index) => ({ column: `c${index}`, operator: "equals" as const, value: "x" }));
      expect(rowRestrictionsSchema.safeParse(many).success).toBe(false);
      const ten = many.slice(0, 10);
      expect(rowRestrictionsSchema.safeParse(ten).success).toBe(true);
    });

    it("rejects a malformed entry", () => {
      expect(rowRestrictionsSchema.safeParse([{ column: "", operator: "equals", value: "true" }]).success).toBe(false);
      expect(rowRestrictionsSchema.safeParse([{ column: "consent", operator: "equals", value: "" }]).success).toBe(false);
      expect(rowRestrictionsSchema.safeParse([{ column: "consent", value: "true" }]).success).toBe(false);
    });
  });
});
