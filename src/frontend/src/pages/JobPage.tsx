import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getJobStatus, sendJobEmail } from "@/api/jobsApi";
import { JobProgress } from "@/components/JobProgress";
import { ArtifactList } from "@/components/ArtifactList";
import { ErrorMessage } from "@/components/ErrorMessage";
import { useI18n } from "@/i18n";

export function JobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { t } = useI18n();

  const { data: job, error, refetch } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "succeeded" || status === "failed") {
        return false;
      }
      return 2500;
    },
  });

  if (error) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-10">
        <ErrorMessage
          message={error instanceof Error ? error.message : t("jobError")}
          onRetry={() => refetch()}
        />
        <Link
          to="/"
          className="inline-block mt-6 text-red-600 font-medium hover:underline"
        >
          {t("newTask")}
        </Link>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-10">
        <div className="flex items-center gap-3 text-gray-500">
          <div className="w-5 h-5 border-2 border-gray-300 border-t-red-600 rounded-full animate-spin" />
          <span>{t("jobLoading")}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-10 space-y-8">
      <h2 className="text-2xl font-bold text-gray-900">{t("jobTitle")}</h2>

      {job.status !== "succeeded" && <JobProgress job={job} />}

      {job.status === "succeeded" && (
        <div className="space-y-8">
          <div className="p-4 bg-green-50 border border-green-200 rounded-xl text-green-800 text-sm font-medium">
            {t("jobSucceeded")}
          </div>

          {/* AI Response */}
          {job.summary && (
            <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-3">
              <div className="flex items-center gap-2">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center">
                  <span className="text-white text-xs font-bold">AI</span>
                </div>
                <h3 className="text-sm font-bold text-gray-900">{t("aiResponseTitle")}</h3>
              </div>
              <div className="text-sm text-gray-700 leading-relaxed whitespace-pre-line pl-9">
                {job.summary}
              </div>
            </div>
          )}

          <ArtifactList artifacts={job.artifacts} />
          <SendEmailSection jobId={job.job_id} artifacts={job.artifacts} />
        </div>
      )}

      {job.status === "failed" && job.error && (
        <ErrorMessage message={job.error.message} />
      )}

      <Link
        to="/"
        className="inline-block text-red-600 font-medium hover:underline"
      >
        {t("newTask")}
      </Link>
    </div>
  );
}

function SendEmailSection({ jobId, artifacts }: { jobId: string; artifacts: { type: string; filename: string; download_url: string }[] }) {
  const { t } = useI18n();
  const [sender, setSender] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [recipientInput, setRecipientInput] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [selectedArtifacts, setSelectedArtifacts] = useState<Set<string>>(
    () => new Set(artifacts.map((a) => a.filename))
  );
  const [extraAttachments, setExtraAttachments] = useState<File[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [sendResult, setSendResult] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  function handleAddRecipient() {
    const email = recipientInput.trim();
    if (!email) return;

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      setInputError(t("recipientInvalid"));
      return;
    }

    if (recipients.includes(email)) {
      setInputError(t("recipientDuplicate"));
      return;
    }

    setInputError(null);
    setRecipients((prev) => [...prev, email]);
    setRecipientInput("");
  }

  function handleRemoveRecipient(index: number) {
    setRecipients((prev) => prev.filter((_, i) => i !== index));
  }

  function handleRecipientKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddRecipient();
    }
  }

  function handleToggleArtifact(filename: string) {
    setSelectedArtifacts((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) {
        next.delete(filename);
      } else {
        next.add(filename);
      }
      return next;
    });
  }

  function handleExtraAttachmentChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const newFiles = Array.from(files).filter(
      (file) => !extraAttachments.some((a) => a.name === file.name)
    );
    setExtraAttachments((prev) => [...prev, ...newFiles]);
    e.target.value = "";
  }

  function handleRemoveExtraAttachment(index: number) {
    setExtraAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSend() {
    if (!sender.trim()) {
      setSendError(t("senderRequired"));
      return;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(sender.trim())) {
      setSendError(t("senderInvalid"));
      return;
    }

    if (recipients.length === 0) {
      setInputError(t("recipientRequired"));
      return;
    }

    setIsSending(true);
    setSendError(null);
    setSendResult(null);

    try {
      const result = await sendJobEmail(jobId, {
        sender: sender.trim(),
        recipients,
        subject,
        body,
        attachments: extraAttachments,
        artifactFilenames: Array.from(selectedArtifacts),
      });
      setSendResult(result.message);
    } catch (err) {
      setSendError(err instanceof Error ? err.message : t("sendFailed"));
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="border-t border-gray-200 pt-8">
      <h3 className="text-lg font-bold text-gray-900 mb-4">{t("sendTitle")}</h3>

      {sendResult && (
        <div className="p-4 bg-green-50 border border-green-200 rounded-xl text-green-800 text-sm font-medium">
          {sendResult}
        </div>
      )}
      {sendError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-800 text-sm font-medium">
          {sendError}
        </div>
      )}

      {!sendResult && (
        <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-5">
          {/* Sender */}
          <div className="space-y-2">
            <label
              htmlFor="email-sender"
              className="block text-xs font-semibold text-gray-500 uppercase tracking-wide"
            >
              {t("senderLabel")}
            </label>
            <input
              id="email-sender"
              type="email"
              placeholder={t("senderPlaceholder")}
              value={sender}
              onChange={(e) => setSender(e.target.value)}
              disabled={isSending}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none disabled:opacity-50 transition-all"
            />
          </div>

          {/* Recipients */}
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide">
              {t("recipientLabel")}
            </label>
            <div className="flex gap-2">
              <input
                type="email"
                placeholder={t("recipientPlaceholder")}
                value={recipientInput}
                onChange={(e) => {
                  setRecipientInput(e.target.value);
                  setInputError(null);
                }}
                onKeyDown={handleRecipientKeyDown}
                disabled={isSending}
                className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none disabled:opacity-50 transition-all"
              />
              <button
                type="button"
                onClick={handleAddRecipient}
                disabled={isSending}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-gray-50 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 transition-colors"
              >
                {t("recipientAdd")}
              </button>
            </div>
            {inputError && (
              <p className="text-sm text-red-600">{inputError}</p>
            )}
            {recipients.length > 0 && (
              <ul className="flex flex-wrap gap-2 mt-2">
                {recipients.map((email, index) => (
                  <li
                    key={email}
                    className="inline-flex items-center gap-1.5 py-1 px-3 bg-red-50 text-red-700 rounded-full text-sm"
                  >
                    <span>{email}</span>
                    <button
                      type="button"
                      onClick={() => handleRemoveRecipient(index)}
                      disabled={isSending}
                      className="w-4 h-4 flex items-center justify-center rounded-full hover:bg-red-200 text-red-500 text-xs transition-colors"
                      aria-label={`${t("remove")} ${email}`}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Subject */}
          <div className="space-y-2">
            <label
              htmlFor="email-subject"
              className="block text-xs font-semibold text-gray-500 uppercase tracking-wide"
            >
              {t("subjectLabel")}
            </label>
            <input
              id="email-subject"
              type="text"
              placeholder={t("subjectPlaceholder")}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={isSending}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none disabled:opacity-50 transition-all"
            />
          </div>

          {/* Body */}
          <div className="space-y-2">
            <label
              htmlFor="email-body"
              className="block text-xs font-semibold text-gray-500 uppercase tracking-wide"
            >
              {t("bodyLabel")}
            </label>
            <textarea
              id="email-body"
              rows={6}
              placeholder={t("bodyPlaceholder")}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={isSending}
              className="w-full rounded-lg border border-gray-300 px-3 py-3 text-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none disabled:opacity-50 transition-all resize-vertical min-h-[100px]"
            />
          </div>

          {/* Attachments */}
          <div className="space-y-3">
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide">
              {t("attachmentLabel")}
            </label>

            {/* Job artifacts as checkboxes */}
            {artifacts.length > 0 && (
              <ul className="space-y-2">
                {artifacts.map((artifact) => (
                  <li key={artifact.filename} className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id={`artifact-${artifact.filename}`}
                      checked={selectedArtifacts.has(artifact.filename)}
                      onChange={() => handleToggleArtifact(artifact.filename)}
                      disabled={isSending}
                      className="w-4 h-4 rounded border-gray-300 text-red-600 focus:ring-red-500"
                    />
                    <label
                      htmlFor={`artifact-${artifact.filename}`}
                      className="text-sm text-gray-800 cursor-pointer"
                    >
                      {artifact.filename}
                    </label>
                  </li>
                ))}
              </ul>
            )}

            {/* Extra attachments upload */}
            <div className="flex items-center gap-3 mt-2">
              <button
                type="button"
                onClick={() => document.getElementById("extra-attachments")?.click()}
                disabled={isSending}
                className="text-sm text-red-600 font-medium hover:text-red-700 transition-colors disabled:opacity-50"
              >
                ＋ {t("uploadExtra")}
              </button>
              <input
                id="extra-attachments"
                type="file"
                multiple
                onChange={handleExtraAttachmentChange}
                disabled={isSending}
                className="hidden"
              />
            </div>

            {extraAttachments.length > 0 && (
              <ul className="mt-2 space-y-1.5">
                {extraAttachments.map((file, index) => (
                  <li
                    key={`${file.name}-${index}`}
                    className="flex items-center justify-between py-2 px-3 bg-gray-50 border border-gray-200 rounded-lg text-sm"
                  >
                    <span className="text-gray-800 truncate mr-2">
                      {file.name}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleRemoveExtraAttachment(index)}
                      disabled={isSending}
                      className="shrink-0 w-6 h-6 flex items-center justify-center rounded-full text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                      aria-label={`${t("remove")} ${file.name}`}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Send button */}
          <button
            type="button"
            onClick={handleSend}
            disabled={isSending || recipients.length === 0}
            className="w-full py-3 px-6 rounded-lg bg-red-600 text-white font-semibold text-base shadow-md hover:bg-red-700 hover:shadow-lg disabled:bg-red-300 disabled:cursor-not-allowed transition-all duration-200"
          >
            {isSending ? t("sending") : t("sendButton")}
          </button>
        </div>
      )}
    </div>
  );
}
