import { useEffect, useState } from "react";
import { getHealth } from "./api";

/**
 * Temporary connectivity check.
 *
 * Confirms the browser can reach the API across origins before any real
 * screens are built -- CORS problems are much easier to diagnose here than
 * behind three components. Replaced by the real UI in the next step.
 */
export default function App() {
  const [status, setStatus] = useState("checking...");
  const [error, setError] = useState(null);

  useEffect(() => {
    getHealth()
      .then((body) => setStatus(body.status))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <main>
      <h1>SmartVerify</h1>
      {error ? (
        <p className="error">Cannot reach the API: {error}</p>
      ) : (
        <p>API health: {status}</p>
      )}
    </main>
  );
}
