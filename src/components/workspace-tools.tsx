"use client";

import { useEffect, useState } from "react";
import { Download, Plus, Trash2, UserPlus } from "lucide-react";
import { AdminTools } from "./admin-tools";
import AuditViewer from "./audit-viewer";
import DatasetPermissions from "./dataset-permissions";
import QueryHistory from "./query-history";
import type { Column, DatasetOption, Organization, WorkspaceDataset } from "./workspace-types";
import type { LedgerEvent } from "@/lib/domain";

type Member = { user_id: string; email: string; role: string; created_at?: string };

function PrivacyLedger({ events, organizationId }: { events: LedgerEvent[]; organizationId: string }) {
  const spent = events.reduce((total, event) => total + event.epsilon, 0);
  return <div className="view-stack stagger">
    <div className="view-title" style={{ "--i": 0 } as React.CSSProperties}><div><span className="kicker">§ 03 — Ledger</span><h1>Privacy ledger</h1><p>Every protected release and its irreversible budget cost.</p></div><a className="button primary" href={`/api/reports?organizationId=${encodeURIComponent(organizationId)}`}><Download size={15} /> Download report</a></div>
    <section className="surface" style={{ "--i": 1 } as React.CSSProperties}><div className="section-heading"><div><h2>Release events</h2><p>{events.length} recent entries for the active dataset</p></div><span className="sync-state"><i /> {spent.toFixed(2)} ε recorded</span></div>
      <div className="table-scroll"><table><thead><tr><th>Released</th><th>Operation</th><th>Dataset</th><th>Actor</th><th className="right">Cost</th><th>Result</th></tr></thead><tbody>
        {events.length ? events.map(event => <tr key={event.id}><td className="cell-meta">{event.time}</td><td className="capitalize cell-title"><strong>{event.operation}</strong></td><td>{event.dataset}</td><td className="cell-meta">{event.actor}</td><td className="cell-num"><strong>{event.epsilon.toFixed(2)} ε</strong></td><td className="cell-meta">{event.result}</td></tr>) : <tr><td colSpan={6} className="empty-cell">No releases yet. The first protected answer will appear here.</td></tr>}
      </tbody></table></div>
    </section>
  </div>;
}

function TeamAccess({ organizationId, role }: { organizationId: string; role: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("analyst");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const canManage = ["owner", "admin"].includes(role);

  async function loadMembers() {
    if (!organizationId || !canManage) return;
    const response = await fetch(`/api/team?organizationId=${encodeURIComponent(organizationId)}`);
    const body = await response.json();
    if (response.ok) setMembers(body.members || []);
    else setMessage(body.error || "Members could not be loaded.");
  }
  useEffect(() => { void loadMembers(); }, [organizationId, canManage]);

  async function invite() {
    setBusy(true); setMessage("");
    const response = await fetch("/api/team", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organizationId, email, role: inviteRole }) });
    const body = await response.json();
    setMessage(response.ok ? "Invitation sent." : body.error || "Invitation failed.");
    if (response.ok) { setEmail(""); await loadMembers(); }
    setBusy(false);
  }

  async function updateRole(member: Member, nextRole: string) {
    const previous = member.role;
    setMessage("");
    setMembers(current => current.map(item => item.user_id === member.user_id ? { ...item, role: nextRole } : item));
    const response = await fetch("/api/team", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organizationId, userId: member.user_id, role: nextRole }) });
    const body = await response.json();
    if (!response.ok) {
      setMembers(current => current.map(item => item.user_id === member.user_id ? { ...item, role: previous } : item));
      setMessage(body.error || "Role update failed. The previous role was restored.");
    } else setMessage(`Updated ${member.email} to ${nextRole}.`);
  }

  async function revoke(member: Member) {
    if (!window.confirm(`Revoke ${member.email}'s access?`)) return;
    const response = await fetch("/api/team", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organizationId, userId: member.user_id }) });
    const body = await response.json().catch(() => ({}));
    if (response.ok) { setMembers(current => current.filter(item => item.user_id !== member.user_id)); setMessage("Member access revoked."); }
    else setMessage(response.status === 405 ? "Member revocation is not enabled by this deployment." : body.error || "Access could not be revoked.");
  }

  return <div className="view-stack stagger"><div className="view-title" style={{ "--i": 0 } as React.CSSProperties}><div><span className="kicker">§ 05 — Access</span><h1>Team access</h1><p>Grant the minimum role needed to work with protected data.</p></div></div>
    <div className="two-column" style={{ "--i": 1 } as React.CSSProperties}><section className="surface"><div className="section-heading"><div><h2>Invite a member</h2><p>Invitations are scoped to this organization.</p></div><UserPlus size={19} /></div>
      <div className="invite-form"><label>Email address<input type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="analyst@example.com" /></label><label>Role<select value={inviteRole} onChange={event => setInviteRole(event.target.value)}><option value="analyst">Analyst</option><option value="admin">Admin</option></select></label></div>
      <button className="button primary" disabled={!canManage || !email || busy} onClick={invite}>{busy ? "Sending invitation..." : "Send invitation"}</button>
      {!canManage && <p className="muted-message">Owner or admin access is required.</p>}
    </section>
    <section className="surface"><div className="section-heading"><div><h2>Members</h2><p>{members.length} active member{members.length === 1 ? "" : "s"}</p></div></div>
      <div className="member-list">{members.map(member => <div className="member-row" key={member.user_id}><div className="member-identity"><span>{member.email?.slice(0, 2).toUpperCase()}</span><div><strong>{member.email}</strong><small>{member.role}</small></div></div><div className="member-actions"><select aria-label={`Role for ${member.email}`} value={member.role} disabled={!canManage || member.role === "owner"} onChange={event => updateRole(member, event.target.value)}><option value="analyst">Analyst</option><option value="admin">Admin</option>{member.role === "owner" && <option value="owner">Owner</option>}</select><button className="icon-button danger-button" disabled={!canManage || member.role === "owner"} onClick={() => revoke(member)} aria-label={`Revoke ${member.email}`}><Trash2 size={16} /></button></div></div>)}{!members.length && <p className="empty-message">No members were returned.</p>}</div>
    </section></div>{message && <p className="status-bar" role="status">{message}</p>}
  </div>;
}

function Settings({ organizationId, organizations, dataset, onOrganizationsChanged }: { organizationId: string; organizations: Organization[]; dataset: WorkspaceDataset; onOrganizationsChanged: (organization?: Organization) => Promise<void> }) {
  const [organizationName, setOrganizationName] = useState("");
  const [policy, setPolicy] = useState({ epsilon_total: dataset.epsilonTotal, delta_total: dataset.deltaTotal, min_group_size: dataset.minGroupSize });
  const [message, setMessage] = useState("");
  const [policyAvailable, setPolicyAvailable] = useState(true);

  useEffect(() => {
    setPolicy({ epsilon_total: dataset.epsilonTotal, delta_total: dataset.deltaTotal, min_group_size: dataset.minGroupSize });
    fetch(`/api/policy?datasetId=${encodeURIComponent(dataset.id)}&organizationId=${encodeURIComponent(organizationId)}`).then(async response => {
      if (response.ok) setPolicy(await response.json());
      else if (response.status === 404 || response.status === 405) setPolicyAvailable(false);
    }).catch(() => setPolicyAvailable(false));
  }, [dataset.id, dataset.deltaTotal, dataset.epsilonTotal, dataset.minGroupSize, organizationId]);

  async function createOrganization() {
    setMessage("");
    const response = await fetch("/api/organizations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: organizationName }) });
    const body = await response.json();
    if (!response.ok) return setMessage(body.error || "Organization could not be created.");
    setOrganizationName(""); setMessage("Organization created.");
    await onOrganizationsChanged({ ...body.organization, role: "owner" });
  }

  async function savePolicy() {
    setMessage("");
    const response = await fetch("/api/policy", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organizationId, datasetId: dataset.id, ...policy }) });
    const body = await response.json().catch(() => ({}));
    if (response.ok) { setMessage("Privacy policy saved."); setPolicyAvailable(true); }
    else { if (response.status === 404 || response.status === 405) setPolicyAvailable(false); setMessage(response.status === 404 || response.status === 405 ? "Policy editing is not enabled by this deployment." : body.error || "Policy could not be saved."); }
  }

  return <div className="view-stack stagger"><div className="view-title" style={{ "--i": 0 } as React.CSSProperties}><div><span className="kicker">§ 07 — Controls</span><h1>Settings</h1><p>Manage organization boundaries and dataset release policy.</p></div></div><div className="two-column" style={{ "--i": 1 } as React.CSSProperties}>
    <section className="surface"><div className="section-heading"><div><h2>Organizations</h2><p>{organizations.length || 1} workspace{organizations.length === 1 ? "" : "s"} available</p></div></div><label>New organization name<input value={organizationName} maxLength={80} onChange={event => setOrganizationName(event.target.value)} placeholder="Research operations" /></label><button className="button primary" disabled={!organizationName.trim()} onClick={createOrganization}><Plus size={15} /> Create organization</button></section>
    <section className="surface"><div className="section-heading"><div><h2>Dataset policy</h2><p>Changes apply to future releases from {dataset.name}.</p></div></div><div className="policy-grid"><label>Total epsilon<input type="number" min={dataset.epsilonUsed} step="0.1" value={policy.epsilon_total} onChange={event => setPolicy(current => ({ ...current, epsilon_total: Number(event.target.value) }))} /></label><label>Delta<input type="number" min="0" step="0.000001" value={policy.delta_total} onChange={event => setPolicy(current => ({ ...current, delta_total: Number(event.target.value) }))} /></label><label>Minimum group size<input type="number" min="1" step="1" value={policy.min_group_size} onChange={event => setPolicy(current => ({ ...current, min_group_size: Number(event.target.value) }))} /></label></div><button className="button primary" onClick={savePolicy}>Save policy</button>{!policyAvailable && <p className="muted-message">Read-only values are shown until the optional policy API is installed.</p>}</section>
  </div>{message && <p className="status-bar" role="status">{message}</p>}</div>;
}

export function WorkspaceTools({ active, organizationId, role, dataset, datasets, columns, events, organizations, onSelectDataset, onRefresh, onOrganizationsChanged }: {
  active: string; organizationId: string; role: string; dataset: WorkspaceDataset; datasets: DatasetOption[]; columns: Column[]; events: LedgerEvent[]; organizations: Organization[];
  onSelectDataset: (datasetId: string) => Promise<void>; onRefresh: () => Promise<void>; onOrganizationsChanged: (organization?: Organization) => Promise<void>;
}) {
  if (active === "Datasets") return <><AdminTools organizationId={organizationId} dataset={dataset} datasets={datasets} columns={columns} onSelect={onSelectDataset} onUploaded={onRefresh} />{["owner", "admin"].includes(role) && <div style={{ marginTop: 24 }}><DatasetPermissions datasetId={dataset.id} /></div>}</>;
  if (active === "Privacy ledger") return <PrivacyLedger events={events} organizationId={organizationId} />;
  if (active === "Release history") return <QueryHistory datasetId={dataset.id} datasetName={dataset.name} role={role} />;
  if (active === "Team access") return <TeamAccess organizationId={organizationId} role={role} />;
  if (active === "Audit log") return <AuditViewer organizationId={organizationId} />;
  if (active === "Settings") return <Settings organizationId={organizationId} organizations={organizations} dataset={dataset} onOrganizationsChanged={onOrganizationsChanged} />;
  return null;
}
