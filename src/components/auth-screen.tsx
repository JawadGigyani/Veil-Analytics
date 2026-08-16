"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";
import { createClient } from "@/lib/supabase/client";

export function AuthScreen() {
  const [mode, setMode] = useState<"signin" | "signup" | "reset">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    const supabase = createClient();
    if(mode === "reset"){const reset=await supabase.auth.resetPasswordForEmail(email,{redirectTo:`${window.location.origin}/reset-password`});setMessage(reset.error?.message||"Check your email for a password reset link.");setBusy(false);return;}
    const response = mode === "signin"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password, options: { data: { display_name: email.split("@")[0] } } });
    if (response.error) { setMessage(response.error.message); setBusy(false); return; }
    if (mode === "signup" && !response.data.session) { setMessage("Check your email to confirm the account, then sign in."); setBusy(false); return; }
    // Deliberately does NOT call /api/bootstrap: the reload below mounts
    // src/app/page.tsx, which provisions the workspace itself. Calling it here
    // as well raced that mount and provisioned duplicate organizations, since
    // seeding takes long enough for the second caller to still see no
    // membership. migration-009 makes the duplicate impossible at the database
    // level; dropping this call keeps the request from being made at all.
    window.location.reload();
  }

  return <main className="auth-shell ledger-grid major">
    <div className="auth-card">
      <section className="auth-pitch">
        <div className="brand">veil<span>.</span></div>
        <h1><LockKeyhole size={30} aria-hidden="true" /><br />Useful answers.<br /><em>Protected people.</em></h1>
        <p>Run aggregate analytics without releasing raw records — and without pretending private answers are exact.</p>
        <div className="auth-badge"><ShieldCheck size={14} aria-hidden="true" /> Differential privacy budget enforced</div>
      </section>

      <section className="auth-form">
        <span className="kicker">{mode === "signin" ? "Access" : mode === "signup" ? "Enrol" : "Recover"}</span>
        <h2>{mode === "signin" ? "Enter your workspace" : mode === "signup" ? "Create your workspace" : "Reset your password"}</h2>
        <p>Email and password authentication through Supabase.</p>

        <form onSubmit={submit}>
          <label>Email<input required type="email" value={email} onChange={event=>setEmail(event.target.value)} placeholder="you@example.com" /></label>
          {mode!=="reset"&&<label>Password<input required minLength={6} type="password" value={password} onChange={event=>setPassword(event.target.value)} placeholder="At least 6 characters" /></label>}
          {message&&<p role="status" className="auth-note">{message}</p>}
          <button disabled={busy} className="button primary">{busy ? "Connecting…" : mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}<ArrowRight size={16} /></button>
        </form>

        <div className="auth-switch">
          <button className="text-button" onClick={()=>{setMode(mode === "signin" ? "signup" : "signin");setMessage("")}}>{mode === "signin" ? "Need an account?" : "Back to sign in"}</button>
          <button className="text-button" onClick={()=>{setMode(mode === "reset" ? "signin" : "reset");setMessage("")}}>{mode === "reset" ? "Back to sign in" : "Forgot password?"}</button>
        </div>
      </section>
    </div>
  </main>;
}
