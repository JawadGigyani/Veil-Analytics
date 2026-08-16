import { describe, expect, it } from "vitest";
import { canAccessDataset, hasImplicitDatasetAccess } from "./dataset-auth";

describe("dataset authorization", () => {
  it("gives owners and admins implicit access", () => {
    expect(hasImplicitDatasetAccess("owner")).toBe(true);
    expect(hasImplicitDatasetAccess("admin")).toBe(true);
    expect(hasImplicitDatasetAccess("analyst")).toBe(false);
  });

  it("allows owners without permission rows", () => {
    expect(canAccessDataset("owner", [], "run_queries")).toBe(true);
    expect(canAccessDataset("admin", [], "view_audit_log")).toBe(true);
  });

  it("requires explicit run_queries for analysts", () => {
    expect(canAccessDataset("analyst", [], "run_queries")).toBe(false);
    expect(canAccessDataset("analyst", ["view_schema"], "run_queries")).toBe(false);
    expect(canAccessDataset("analyst", ["run_queries"], "run_queries")).toBe(true);
  });

  it("checks the requested permission name", () => {
    expect(canAccessDataset("analyst", ["view_audit_log"], "view_audit_log")).toBe(true);
    expect(canAccessDataset("analyst", ["run_queries"], "view_audit_log")).toBe(false);
  });
});
