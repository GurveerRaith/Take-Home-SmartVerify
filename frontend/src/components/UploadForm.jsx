import { useEffect, useRef, useState } from "react";
import { uploadPolicy } from "../api";

/**
 * Upload a Cedar policy file to the selected tenant.
 *
 * Rejections are shown using the server's own message rather than a generic
 * failure. The API returns a specific, actionable `detail` for every case it
 * refuses -- an invalid policy, a duplicate name, a file that is too large --
 * so passing it straight through is both simpler and more useful than
 * reclassifying status codes here.
 */
export default function UploadForm({ token, tenantId, onUploaded }) {
  // Needed to clear the native file input after a successful upload; its
  // value cannot be reset through React state alone.
  const inputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [busy, setBusy] = useState(false);

  // Messages refer to the tenant they happened in, so drop them on a switch.
  useEffect(() => {
    setError(null);
    setSuccess(null);
  }, [tenantId]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!file || busy) return;

    setError(null);
    setSuccess(null);
    setBusy(true);

    try {
      const created = await uploadPolicy(token, tenantId, file);
      setSuccess(`${created.filename} uploaded.`);
      clearSelection();
      onUploaded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function clearSelection() {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <section className="upload">
      <form onSubmit={handleSubmit}>
        <label htmlFor="policy-file">Upload a policy file</label>

        <div className="upload-row">
          {/*
            `accept` is a convenience for the file picker, not a control: the
            filename pattern and Cedar validation are enforced by the server,
            which is what actually decides.
          */}
          <input
            id="policy-file"
            ref={inputRef}
            type="file"
            accept=".cedar"
            onChange={(event) => {
              setFile(event.target.files[0] ?? null);
              setError(null);
              setSuccess(null);
            }}
          />
          <button type="submit" disabled={!file || busy}>
            {busy ? "Uploading..." : "Upload"}
          </button>
        </div>
      </form>

      {error && <p className="error">{error}</p>}
      {success && <p className="success">{success}</p>}
    </section>
  );
}
