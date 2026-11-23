import { useState, type FormEvent } from "react";
import { Logo } from "../components/Layout";
import { ErrorAlert, Field } from "../components/ui";
import { useAuth } from "../lib/auth";

export function LoginPage() {
  const { login, setup, needsSetup } = useAuth();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (needsSetup) await setup(email, name, password);
      else await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="card">
        <div className="row">
          <Logo size={34} />
          <div>
            <h1>CloudSentinel</h1>
            <div className="muted small">Infrastructure health intelligence</div>
          </div>
        </div>
        {needsSetup && (
          <div className="alert ok" style={{ marginTop: 18 }}>
            Welcome! Create the first administrator account to finish setting up this installation.
          </div>
        )}
        <form onSubmit={submit} aria-label={needsSetup ? "Create administrator" : "Sign in"}>
          <Field label="Email">
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          {needsSetup && (
            <Field label="Name">
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          )}
          <Field label="Password" hint={needsSetup ? "At least 10 characters." : undefined}>
            <input
              type="password"
              autoComplete={needsSetup ? "new-password" : "current-password"}
              value={password}
              minLength={needsSetup ? 10 : undefined}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>
          <ErrorAlert error={error} />
          <button className="btn primary" type="submit" disabled={busy} style={{ justifyContent: "center" }}>
            {busy ? "Please wait…" : needsSetup ? "Create administrator" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
