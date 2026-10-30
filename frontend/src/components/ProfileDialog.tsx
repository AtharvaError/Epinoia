import { useState } from "react";

import { Dialog, ErrorNote, Field, useAction } from "./ui";
import { api, type User } from "../lib/api";

export function ProfileDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const [profile, setProfile] = useState({ name: user.name, phone: user.phone ?? "" });
  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const save = useAction(() => api("/api/auth/me", { method: "PATCH", body: JSON.stringify({ name: profile.name, phone: profile.phone || null }) }), { success: "Profile saved" });
  const change = useAction(() => api("/api/auth/password", { method: "POST", body: JSON.stringify(passwords) }), {
    success: "Password changed. Other devices have been signed out.", onDone: () => setPasswords({ current_password: "", new_password: "" }),
  });
  return (
    <Dialog title="Your profile" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted small">{user.email}, {user.role.name}</p>
        <Field label="Name"><input required minLength={2} value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} /></Field>
        <Field label="Phone"><input value={profile.phone} onChange={(e) => setProfile({ ...profile, phone: e.target.value })} /></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Save profile</button>
      </form>
      <form className="form" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
        <h3>Change password</h3>
        <Field label="Current password"><input type="password" required autoComplete="current-password" value={passwords.current_password} onChange={(e) => setPasswords({ ...passwords, current_password: e.target.value })} /></Field>
        <Field label="New password" hint="At least 10 characters"><input type="password" required minLength={10} maxLength={72} autoComplete="new-password" value={passwords.new_password} onChange={(e) => setPasswords({ ...passwords, new_password: e.target.value })} /></Field>
        <ErrorNote error={change.error} />
        <button className="button-secondary" disabled={change.isPending}>Change password</button>
      </form>
    </Dialog>
  );
}
