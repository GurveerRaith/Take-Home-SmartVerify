import { useState } from "react";
import { getMe } from "../api";

/**
 * Sign-in screen.
 *
 * There is no password and no login flow: authentication is a seeded bearer
 * token, so signing in means pasting one. See DESIGN.md decision 11.
 *
 * The token is verified against /api/me before being accepted, so a bad token
 * fails here with a clear message rather than causing a confusing 401 on the
 * first real request.
 */
export default function Login({ onSignIn }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    try {
      const trimmed = token.trim();
      const me = await getMe(trimmed);
      // Hand the verified identity up so the app does not fetch it twice.
      onSignIn(trimmed, me);
    } catch (err) {
      setError(
        err.status === 401
          ? "That token was not recognised."
          : `Could not sign in: ${err.message}`,
      );
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h1>SmartVerify</h1>
      <p className="muted">
        Sign in with your API token to manage Cedar policy files.
      </p>

      <form onSubmit={handleSubmit}>
        <label htmlFor="token">API token</label>
        <input
          id="token"
          type="text"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="alice_token"
          autoComplete="off"
          autoFocus
        />

        <button type="submit" disabled={busy || token.trim() === ""}>
          {busy ? "Checking..." : "Sign in"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {/*
        Listed so the project can be tried without opening the README. These
        are seeded development credentials, documented there as well.
      */}
      <p className="muted hint">
        Seeded tokens: <code>alice_token</code> (two tenants),{" "}
        <code>bob_token</code> (one), <code>carol_token</code> (a different
        customer).
      </p>
    </div>
  );
}
