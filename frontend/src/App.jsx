import { useEffect, useState } from "react";
import { getMe } from "./api";
import Login from "./components/Login";
import PolicyList from "./components/PolicyList";
import UploadForm from "./components/UploadForm";

// Persisted so a page refresh does not sign the user out. localStorage is
// readable by any script on the page, which is acceptable for seeded demo
// credentials; an httpOnly cookie would be the production choice, and would
// bring CSRF handling with it.
const TOKEN_KEY = "smartverify.token";

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [me, setMe] = useState(null);
  const [tenantId, setTenantId] = useState(null);
  const [restoring, setRestoring] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));
  // Bumped after an upload or delete to make the list refetch. Simpler than
  // lifting the file list itself into this component.
  const [reloadKey, setReloadKey] = useState(0);

  // Restore a session from a stored token. Only runs when the app loads with a
  // token already present -- signing in sets `me` directly, so this does not
  // fetch it a second time.
  useEffect(() => {
    if (!token || me) return;

    // StrictMode runs effects twice in development, and the token could change
    // mid-flight. This flag stops a stale response overwriting current state.
    let cancelled = false;

    getMe(token)
      .then((body) => {
        if (cancelled) return;
        setMe(body);
        setTenantId(body.tenants[0]?.id ?? null);
      })
      .catch(() => {
        // The stored token is no longer valid; drop it and show sign-in again.
        if (!cancelled) {
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
        }
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });

    return () => {
      cancelled = true;
    };
  }, [token, me]);

  function handleSignIn(newToken, identity) {
    localStorage.setItem(TOKEN_KEY, newToken);
    setToken(newToken);
    setMe(identity);
    setTenantId(identity.tenants[0]?.id ?? null);
    setRestoring(false);
  }

  function handleSignOut() {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setMe(null);
    setTenantId(null);
  }

  if (restoring) {
    return (
      <main>
        <p className="muted">Loading...</p>
      </main>
    );
  }

  if (!token || !me) {
    return (
      <main>
        <Login onSignIn={handleSignIn} />
      </main>
    );
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>SmartVerify</h1>
          <p className="muted">
            {me.email} · {me.customer}
          </p>
        </div>
        <button type="button" className="secondary" onClick={handleSignOut}>
          Sign out
        </button>
      </header>

      <section className="toolbar">
        <label htmlFor="tenant">Tenant</label>
        {/*
          Populated from /api/me, which returns only tenants this user holds a
          grant on -- never every tenant belonging to their customer. Bob sees
          one entry here, Alice sees two.
        */}
        <select
          id="tenant"
          value={tenantId ?? ""}
          onChange={(event) => setTenantId(event.target.value)}
        >
          {me.tenants.map((tenant) => (
            <option key={tenant.id} value={tenant.id}>
              {tenant.name}
            </option>
          ))}
        </select>
      </section>

      {me.tenants.length === 0 ? (
        <p className="muted">You do not have access to any tenants.</p>
      ) : (
        <>
          <UploadForm
            token={token}
            tenantId={tenantId}
            onUploaded={() => setReloadKey((n) => n + 1)}
          />
          <PolicyList
            token={token}
            tenantId={tenantId}
            reloadKey={reloadKey}
            onChanged={() => setReloadKey((n) => n + 1)}
          />
        </>
      )}
    </main>
  );
}
