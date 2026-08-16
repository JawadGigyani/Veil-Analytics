"use client";
import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

export default function ResetPassword() {
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  return <main className="auth-shell ledger-grid major">
    <form
      className="setup-error"
      onSubmit={async event => {
        event.preventDefault();
        const { error } = await createClient().auth.updateUser({ password });
        setMessage(error?.message || "Password updated. You can return to the workspace.");
      }}
    >
      <span className="kicker">Recovery</span>
      <h1>Set a new password</h1>
      <p>Choose at least six characters.</p>
      <label style={{ marginTop: 24 }}>New password<input required minLength={6} type="password" value={password} onChange={event => setPassword(event.target.value)} /></label>
      <div><button className="button primary" style={{ width: "100%" }}>Update password</button></div>
      {message && <p className="status-bar" role="status" style={{ marginTop: 18 }}>{message}</p>}
    </form>
  </main>;
}
