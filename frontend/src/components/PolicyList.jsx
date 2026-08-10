import { useEffect, useState } from "react";
import { deletePolicy, downloadPolicy, listPolicies } from "../api";

/**
 * The policy files in the selected tenant.
 *
 * Refetches whenever the tenant changes, so switching tenants can never show
 * one tenant's files under another's name. `reloadKey` is bumped by the parent
 * after an upload to force a refresh.
 */
export default function PolicyList({ token, tenantId, reloadKey, onChanged }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Which row has an action in flight, so only its buttons are disabled.
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    if (!tenantId) return undefined;

    // Guards against a slow response for the previous tenant arriving after a
    // faster one for the new tenant and overwriting it.
    let cancelled = false;

    setLoading(true);
    setError(null);

    listPolicies(token, tenantId)
      .then((rows) => {
        if (!cancelled) setFiles(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [token, tenantId, reloadKey]);

  async function handleDownload(file) {
    setError(null);
    setBusyId(file.id);
    try {
      await downloadPolicy(token, tenantId, file.id, file.filename);
    } catch (err) {
      setError(`Could not download ${file.filename}: ${err.message}`);
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(file) {
    if (!window.confirm(`Delete ${file.filename}?`)) return;

    setError(null);
    setBusyId(file.id);
    try {
      await deletePolicy(token, tenantId, file.id);
      onChanged();
    } catch (err) {
      setError(`Could not delete ${file.filename}: ${err.message}`);
      setBusyId(null);
    }
  }

  if (loading) return <p className="muted">Loading files...</p>;

  return (
    <>
      {error && <p className="error">{error}</p>}

      {files.length === 0 ? (
        <p className="muted">
          No policy files in this tenant yet. Upload one to get started.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Size</th>
              <th>Uploaded by</th>
              <th>Uploaded</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {files.map((file) => (
              <tr key={file.id}>
                <td>
                  <code>{file.filename}</code>
                </td>
                <td className="muted">{formatSize(file.size_bytes)}</td>
                <td className="muted">{file.uploaded_by}</td>
                <td className="muted">{formatDate(file.uploaded_at)}</td>
                <td className="actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => handleDownload(file)}
                    disabled={busyId === file.id}
                  >
                    Download
                  </button>
                  <button
                    type="button"
                    className="secondary danger"
                    onClick={() => handleDelete(file)}
                    disabled={busyId === file.id}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDate(iso) {
  return new Date(iso).toLocaleString();
}
