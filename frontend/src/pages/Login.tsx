import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { api, type User } from "../lib/api";
import { useMe } from "../lib/auth";

export function Login() {
  const me = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const signIn = useMutation({
    mutationFn: () => api<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
    onSuccess: (user) => {
      queryClient.setQueryData(["me"], user);
      navigate("/", { replace: true });
    },
  });

  if (me.data) return <Navigate to="/" replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    signIn.mutate();
  };

  return (
    <main className="login">
      <section className="login-hero">
        <div className="brand brand-light"><span className="brand-mark" aria-hidden="true" />FestOS Command</div>
        <h1>One command center. Every event. Zero chaos.</h1>
        <p>See what a change will break before you make it.</p>
        <svg className="login-wave" viewBox="0 0 800 260" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 90C120 140 190 215 250 260H0V90Z" />
          <path d="M800 120C680 135 560 190 480 260H800V120Z" />
        </svg>
      </section>

      <section className="login-panel">
        <form onSubmit={submit} className="login-form">
          <h2>Sign in</h2>
          <p className="muted">Core team access for Aarohan 2026.</p>
          <label>
            Email
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {signIn.isError && <p className="form-error" role="alert">{signIn.error.message}</p>}
          <button className="button-primary" disabled={signIn.isPending}>
            {signIn.isPending ? "Signing in" : "Sign in"}
          </button>
          <p className="muted small">Demo accounts are listed in docs/TEST_CREDENTIALS.md.</p>
        </form>
      </section>
    </main>
  );
}
