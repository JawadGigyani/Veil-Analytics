"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, BarChart3, Database, FileCheck2, FileSearch, History, Menu, Plus, Settings, ShieldCheck, UserRound, X } from "lucide-react";
import { Mark } from "@/components/backdrop";
import { AuthScreen } from "@/components/auth-screen";
import { QueryComposer } from "@/components/query-composer";
import { ResultPanel } from "@/components/result-panel";
import { WorkspaceTools } from "@/components/workspace-tools";
import type { Column, DatasetOption, Organization, QueryResult, QueryType, WorkspaceDataset } from "@/components/workspace-types";
import { createClient } from "@/lib/supabase/client";
import { type LedgerEvent } from "@/lib/domain";

const RAIL_STORAGE_KEY = "veil:rail-collapsed";

const navigation = [
  { label: "Overview", icon: BarChart3 },
  { label: "Datasets", icon: Database },
  { label: "Privacy ledger", icon: ShieldCheck },
  { label: "Release history", icon: History },
  { label: "Team access", icon: UserRound },
  { label: "Audit log", icon: FileSearch },
  { label: "Settings", icon: Settings },
];


/**
 * Placeholder state shown until the real workspace loads.
 *
 * Every figure here is zero on purpose. An earlier version seeded this from a
 * fabricated demo dataset -- 12,480 rows, 1.8 epsilon already spent, and a
 * ledger of invented releases attributed to invented people. If the workspace
 * fetch failed, a user saw those as real. For a tool whose whole claim is an
 * auditable record of what was released and what it cost, inventing that
 * record is the worst possible default.
 */
const emptyDataset: WorkspaceDataset = {
  id: "",
  name: "No dataset loaded",
  description: "Sign in and configure Supabase to load a protected dataset.",
  rows: 0,
  columns: 0,
  epsilonTotal: 0,
  epsilonUsed: 0,
  updated: "never",
  status: "Processing",
  minGroupSize: 5,
  publicMinDenominator: 10,
  deltaTotal: 0,
  deltaUsed: 0,
  allowedQueryTypes: [],
  entityColumn: null,
  maxContributions: 1,
};

export default function Home() {
  const [active, setActive] = useState("Overview");
  const [mobileOpen, setMobileOpen] = useState(false);
  // Desktop rail collapse. Starts expanded on both server and first client
  // render so hydration matches; the stored preference is applied in an
  // effect immediately after.
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [queryOpen, setQueryOpen] = useState(false);
  const [dataset, setDataset] = useState(emptyDataset);
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [events, setEvents] = useState<LedgerEvent[]>([]);
  const [columns, setColumns] = useState<Column[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [role, setRole] = useState("owner");
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [initializationError, setInitializationError] = useState("");
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [resultType, setResultType] = useState<QueryType>("count");
  const configured = Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

  const loadWorkspace = useCallback(async (orgId: string, datasetId: string) => {
    if (!configured) return;
    setWorkspaceBusy(true);
    try {
      const response = await fetch("/api/workspace", { headers: { "x-organization-id": orgId, "x-dataset-id": datasetId } });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "The workspace could not be loaded.");
      setInitializationError("");
      setOrganizationId(payload.organizationId);
      setDatasets(payload.datasets || []);
      setUserEmail(payload.user.email);
      setRole(payload.user.role);
      setDataset({
        id: payload.dataset.id,
        name: payload.dataset.name,
        description: payload.dataset.description || "Protected tabular dataset",
        rows: Number(payload.dataset.row_count),
        columns: Number(payload.dataset.column_count),
        epsilonTotal: Number(payload.policy.epsilon_total),
        epsilonUsed: Number(payload.policy.epsilon_used),
        minGroupSize: Number(payload.policy.min_group_size || 5),
        publicMinDenominator: Number(payload.policy.public_min_denominator || 10),
        deltaTotal: Number(payload.policy.delta_total || 0),
        deltaUsed: Number(payload.policy.delta_used || 0),
        allowedQueryTypes: payload.policy.allowed_query_types || ["count", "grouped_count", "mean", "bounded_sum", "histogram", "top_k"],
        entityColumn: payload.policy.entity_column ?? null,
        maxContributions: Number(payload.policy.max_contributions || 1),
        updated: payload.dataset.updated_at ? new Date(payload.dataset.updated_at).toLocaleString() : "just now",
        status: payload.dataset.status === "processing" ? "Processing" : "Protected",
      });
      setColumns(payload.columns || []);
      setEvents((payload.ledger || []).map((event: { id: string; operation: string; epsilon_spent: number; created_at: string; query_id?: string }) => ({
        id: event.id,
        operation: event.operation.replaceAll("_", " "),
        dataset: payload.dataset.name,
        epsilon: Number(event.epsilon_spent),
        result: event.query_id ? `Release ${event.query_id.slice(0, 8)}` : "Protected release",
        actor: "You",
        time: new Date(event.created_at).toLocaleString(),
      })));
    } catch (cause) {
      setInitializationError(cause instanceof Error ? cause.message : "The workspace could not be loaded.");
    } finally {
      setWorkspaceBusy(false);
    }
  }, [configured]);

  const loadOrganizations = useCallback(async () => {
    if (!configured) return;
    const response = await fetch("/api/organizations");
    const body = await response.json();
    if (response.ok) setOrganizations(body.organizations || []);
  }, [configured]);

  useEffect(() => {
    if (!configured) { setAuthChecked(true); return; }
    const client = createClient();
    client.auth.getUser().then(async ({ data }) => {
      setUserEmail(data.user?.email ?? null);
      if (data.user) {
        const bootstrap = await fetch("/api/bootstrap", { method: "POST" });
        const body = await bootstrap.json();
        if (!bootstrap.ok) setInitializationError(body.error || "Workspace initialization failed.");
        await loadOrganizations();
        if (bootstrap.ok) await loadWorkspace(body.organizationId || "", body.datasetId || "");
      }
      setAuthChecked(true);
    }).catch(cause => { setInitializationError(cause instanceof Error ? cause.message : "Workspace initialization failed."); setAuthChecked(true); });
  }, [configured, loadOrganizations, loadWorkspace]);

  useEffect(() => {
    try { setRailCollapsed(window.localStorage.getItem(RAIL_STORAGE_KEY) === "1"); }
    catch { /* storage blocked; the default expanded rail is fine */ }
  }, []);

  function toggleRail() {
    setRailCollapsed(current => {
      const next = !current;
      try { window.localStorage.setItem(RAIL_STORAGE_KEY, next ? "1" : "0"); }
      catch { /* storage blocked; the toggle still works for this session */ }
      return next;
    });
  }

  async function handleOrganizationChange(nextId: string) {
    setOrganizationId(nextId);
    setResult(null);
    await loadWorkspace(nextId, "");
  }

  /**
   * Switching sections swaps the content but leaves the scroll offset where
   * the previous section left it. Going from a long section (Overview with a
   * released answer) to a short one (Audit log) therefore lands the viewport
   * below everything the new section rendered, and the section reads as
   * broken and empty rather than merely scrolled. Every navigation entry
   * point routes through here so the reset cannot be forgotten at one of
   * them.
   */
  function showSection(label: string) {
    setActive(label);
    setMobileOpen(false);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "auto" });
  }

  async function handleDatasetChange(nextId: string) {
    setResult(null);
    await loadWorkspace(organizationId, nextId);
  }

  async function handleReleased(nextResult: QueryResult, nextType: QueryType) {
    setResult(nextResult);
    setResultType(nextType);
    setActive("Overview");
    await loadWorkspace(organizationId, dataset.id);
  }

  async function handleOrganizationsChanged(organization?: Organization) {
    await loadOrganizations();
    if (organization) await handleOrganizationChange(organization.id);
  }

  if (!authChecked) return <main className="center-state" aria-live="polite">Checking your workspace</main>;
  if (configured && !userEmail) return <AuthScreen />;
  if (initializationError && configured && !dataset.id) return <SetupError message={initializationError} />;

  const remaining = Math.max(0, dataset.epsilonTotal - dataset.epsilonUsed);
  const usedPercent = dataset.epsilonTotal ? Math.min(100, dataset.epsilonUsed / dataset.epsilonTotal * 100) : 0;

  return <div className={`app-shell ${railCollapsed ? "rail-collapsed" : ""}`}>
    <aside id="workspace-navigation" className={`workspace-rail ${mobileOpen ? "open" : ""} ${railCollapsed ? "collapsed" : ""}`}>
      <div className="brand-row"><div><div className="brand">veil<span>.</span></div><div className="brand-subtitle">privacy analytics</div></div><button className="icon-button mobile-only" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={19} /></button></div>
      <nav aria-label="Workspace navigation">{navigation.map(({ label, icon: Icon }, index) => <button key={label} className={active === label ? "active" : ""} title={railCollapsed ? label : undefined} onClick={() => showSection(label)}><b className="nav-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</b><Icon size={16} /><span>{label}</span>{label === "Privacy ledger" && <small>{events.length}</small>}</button>)}</nav>
      <div className="rail-account"><div className="avatar">{userEmail?.slice(0, 2).toUpperCase() || "DM"}</div><div><strong>{userEmail || "Demo workspace"}</strong><span>{userEmail ? role : "Non-persistent preview"}</span></div></div>
      {/* Standing readout of the two numbers that constrain every action here. */}
      <div className="rail-console" aria-hidden="true">
        budget <b>{Math.max(0, dataset.epsilonTotal - dataset.epsilonUsed).toFixed(1)} ε</b> / {dataset.epsilonTotal.toFixed(1)}<br />
        releases <b>{events.length}</b> · fields <b>{dataset.columns}</b><br />
        <span>{workspaceBusy ? "syncing" : "ready"}</span> <i />
      </div>
    </aside>
    {mobileOpen && <button className="mobile-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}

    <main className="workspace-main">
      <header className="workspace-header">
        <div className="header-location">
          <button className="icon-button mobile-only" onClick={() => setMobileOpen(true)} aria-label="Open navigation" aria-controls="workspace-navigation"><Menu size={21} /></button>
          <button className="icon-button desktop-only" onClick={toggleRail} aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"} aria-expanded={!railCollapsed} aria-controls="workspace-navigation" title={railCollapsed ? "Expand navigation" : "Collapse navigation"}><Menu size={21} /></button>
          <span>Workspace</span><strong>/ {active}</strong>
        </div>
        <div className="header-actions">
          <label className="header-select"><span>Organization</span><select aria-label="Active organization" value={organizationId} disabled={workspaceBusy || !organizations.length} onChange={event => handleOrganizationChange(event.target.value)}>{organizations.length ? organizations.map(item => <option key={item.id} value={item.id}>{item.name}</option>) : <option value="">Demo organization</option>}</select></label>
          <label className="header-select"><span>Dataset</span><select aria-label="Active dataset" value={dataset.id} disabled={workspaceBusy || !datasets.length} onChange={event => handleDatasetChange(event.target.value)}>{datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <button className="button primary" onClick={() => setQueryOpen(true)}><Plus size={16} /><span>New query</span></button>
        </div>
      </header>

      <div className="workspace-content" aria-busy={workspaceBusy}>
        <div className="live-region" aria-live="polite">{workspaceBusy ? "Loading workspace" : initializationError}</div>
        {initializationError && <div className="status-bar error-message" role="alert">{initializationError} <button className="text-button" onClick={() => loadWorkspace(organizationId, dataset.id)}>Retry</button></div>}
        {active === "Overview" ? <Overview dataset={dataset} events={events} remaining={remaining} usedPercent={usedPercent} result={result} resultType={resultType} onQuery={() => setQueryOpen(true)} onNavigate={setActive} /> : <WorkspaceTools active={active} organizationId={organizationId} role={role} dataset={dataset} datasets={datasets} columns={columns} events={events} organizations={organizations} onSelectDataset={handleDatasetChange} onRefresh={() => loadWorkspace(organizationId, dataset.id)} onOrganizationsChanged={handleOrganizationsChanged} />}
      </div>
    </main>
    <QueryComposer open={queryOpen} onClose={() => setQueryOpen(false)} dataset={dataset} columns={columns} organizationId={organizationId} onReleased={handleReleased} />
  </div>;
}

function Overview({ dataset, events, remaining, usedPercent, result, resultType, onQuery, onNavigate }: { dataset: WorkspaceDataset; events: LedgerEvent[]; remaining: number; usedPercent: number; result: QueryResult | null; resultType: QueryType; onQuery: () => void; onNavigate: (view: string) => void }) {
  const remainingDelta = Math.max(0, dataset.deltaTotal - dataset.deltaUsed);
  return <div className="view-stack stagger">
    <div className="view-title overview-title" style={{ "--i": 0 } as React.CSSProperties}>
      <div><span className="kicker">§ 01 — Workspace</span><h1>Protected analytics workspace</h1><p>Release useful aggregate answers without exposing exact results or raw records.</p></div>
      <span className="sync-state"><i /> Synced {dataset.updated}</span>
    </div>

    {/* Standing reminder of the three rules the whole product enforces. */}
    <div className="ticker" style={{ "--i": 1 } as React.CSSProperties} aria-hidden="true">
      {[0, 1].map(track => <div className="ticker-track" key={track}>
        <span>Every release spends budget <b>permanently</b></span><em>✳</em>
        <span>Exact aggregates <b>never leave</b> the protected environment</span><em>✳</em>
        <span>Noise is calibrated to the <b>privacy unit</b>, not the row</span><em>✳</em>
        <span>Budget is <b>not refunded</b> when a release fails</span><em>✳</em>
      </div>)}
    </div>

    <section className="overview-band" style={{ "--i": 2 } as React.CSSProperties}>
      <div className="dataset-summary ledger-grid major"><div className="status-label"><ShieldCheck size={14} /> Active dataset</div><h2>{dataset.name}</h2><p>{dataset.description}</p><div className="dataset-facts"><strong>{dataset.rows.toLocaleString()} rows</strong><strong>{dataset.columns} fields</strong><span>{dataset.status}</span></div></div>
      <div className="budget-summary"><div className="summary-label">Privacy budget</div><div className="budget-number tabular">{remaining.toFixed(1)} <span>ε remaining</span></div><div className="budget-track"><span style={{ width: `${usedPercent}%` }} /></div><div className="budget-scale"><span>{dataset.epsilonUsed.toFixed(1)} spent</span><span>{dataset.epsilonTotal.toFixed(1)} total</span></div></div>
      <div className="policy-summary"><div className="summary-label">Release controls</div><dl><div><dt>Mechanism</dt><dd>Laplace / Gaussian</dd></div><div><dt>Delta remaining</dt><dd>{remainingDelta.toExponential(1)} δ</dd></div><div><dt>Minimum group</dt><dd>{dataset.minGroupSize} records</dd></div><div><dt>Allowed operations</dt><dd>{dataset.allowedQueryTypes.length}</dd></div></dl></div>
    </section>

    <section className="question-section" style={{ "--i": 3 } as React.CSSProperties}>
      <div className="section-heading"><div><span className="kicker plain">§ 02 — Compose</span><h2>Start with a protected question</h2><p>Review cost and expected uncertainty before reserving budget.</p></div><Mark shape="star" size={22} /></div>
      <div className="operation-list">
        <button onClick={onQuery}><BarChart3 size={20} /><span><strong>Compare groups</strong><small>Release noisy counts grouped by an approved field.</small></span><Plus size={18} /></button>
        <button onClick={onQuery}><Activity size={20} /><span><strong>Measure a distribution</strong><small>Release a bounded mean or histogram.</small></span><Plus size={18} /></button>
      </div>
    </section>

    {result && <ResultPanel result={result} type={resultType} />}

    <section className="surface" style={{ "--i": 4 } as React.CSSProperties}>
      <div className="section-heading"><div><span className="kicker plain">§ 03 — Ledger</span><h2>Recent releases</h2><p>The ledger records every budget reservation.</p></div><button className="text-button" onClick={() => onNavigate("Privacy ledger")}>Open full ledger →</button></div>
      <div className="event-list">{events.length ? events.slice(0, 4).map(event => <div className="event-row" key={event.id}><span className="event-icon"><FileCheck2 size={16} /></span><span><strong className="capitalize">{event.operation}</strong><small>{event.time} · {event.actor}</small></span><span className="event-result">{event.result}</span><strong className="event-cost tabular">−{event.epsilon.toFixed(2)} ε</strong></div>) : <p className="empty-message">No releases yet. The first protected answer will appear here.</p>}</div>
    </section>
  </div>;
}

function SetupError({ message }: { message: string }) {
  return <main className="center-state"><section className="setup-error"><span className="kicker">Setup halted</span><h1>Workspace setup needs attention</h1><p>{message}</p><div><button className="button primary" onClick={() => window.location.reload()}>Retry setup</button><button className="button secondary" onClick={async () => { await createClient().auth.signOut(); window.location.reload(); }}>Sign out</button></div></section></main>;
}
