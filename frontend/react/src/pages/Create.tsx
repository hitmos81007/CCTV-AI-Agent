import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createCase } from "../api";
import { ErrorBanner, PageHeader } from "../components/Ui";

export function Create() {
  const navigate = useNavigate();
  const [description, setDescription] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!authorized) {
      setError("Confirm that all footage for this investigation is authorized.");
      return;
    }
    setBusy(true);
    try {
      const caseId = `CASE-${Date.now()}`;
      await createCase({
        case_id: caseId,
        description: description.trim(),
        initial_zone: "upload-batch",
        neighbors: { "upload-batch": [] },
      });
      navigate(`/cases/${encodeURIComponent(caseId)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create case.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="New Investigation"
        subtitle="Create an authorized investigation draft (batch video ingestion follows in Phase 4)"
      />
      {error && <ErrorBanner message={error} />}

      <form
        onSubmit={handleSubmit}
        className="max-w-2xl space-y-4 rounded-xl border border-[#33465A] bg-[#142133] p-6"
      >
        <label className="block text-xs font-medium text-[#A8B6C9]">
          Visible Object / Vehicle Description
          <textarea
            rows={3}
            required
            minLength={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Example: Red sedan or blue bicycle in staged clips"
            className="mt-1.5 w-full rounded-lg border border-[#33465A] bg-[#0B111C] px-3 py-2 text-sm text-[#F1F5F9]"
          />
        </label>

        <label className="flex items-start gap-2.5 text-xs text-[#A8B6C9]">
          <input
            type="checkbox"
            checked={authorized}
            onChange={(e) => setAuthorized(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            I confirm this investigation uses synthetic, staged, or expressly authorized footage only.
          </span>
        </label>

        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-[#5EE0CA] px-5 py-2.5 text-xs font-semibold text-[#082D2D] hover:bg-[#78E5DC] disabled:opacity-50"
        >
          {busy ? "Creating..." : "Create Draft Case"}
        </button>
      </form>
    </div>
  );
}
