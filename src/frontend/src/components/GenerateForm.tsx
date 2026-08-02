import { useState, useRef } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useI18n } from "@/i18n";

const generateFormSchema = z.object({
  prompt: z.string().min(1, "required"),
});

type GenerateFormData = z.infer<typeof generateFormSchema>;

interface GenerateFormProps {
  onSubmit: (files: File[], prompt: string, template: File | null) => void;
  isSubmitting?: boolean;
}

export function GenerateForm({ onSubmit, isSubmitting }: GenerateFormProps) {
  const { t } = useI18n();
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [template, setTemplate] = useState<File | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const templateInputRef = useRef<HTMLInputElement>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<GenerateFormData>({
    resolver: zodResolver(generateFormSchema),
  });

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files;
    if (!selected || selected.length === 0) return;

    const newFiles: File[] = [];
    for (let i = 0; i < selected.length; i++) {
      const file = selected[i];
      const extension = file.name.split(".").pop()?.toLowerCase();
      const allowedExtensions = [
        "xlsx",
        "csv",
        "txt",
        "pdf",
        "png",
        "jpg",
        "jpeg",
      ];
      if (!extension || !allowedExtensions.includes(extension)) {
        setFileError(t("fileError"));
        return;
      }
      if (!files.some((f) => f.name === file.name)) {
        newFiles.push(file);
      }
    }

    setFileError(null);
    setFiles((prev) => [...prev, ...newFiles]);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleRemoveFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  function handleTemplateChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0];
    if (!selected) return;

    if (selected.name.split(".").pop()?.toLowerCase() !== "pptx") {
      setTemplate(null);
      setTemplateError(t("templateError"));
    } else {
      setTemplate(selected);
      setTemplateError(null);
    }

    if (templateInputRef.current) {
      templateInputRef.current.value = "";
    }
  }

  function handleFormSubmit(data: GenerateFormData) {
    if (files.length === 0) {
      setFileError(t("fileRequired"));
      return;
    }
    onSubmit(files, data.prompt, template);
  }

  return (
    <form
      onSubmit={handleSubmit(handleFormSubmit)}
      className="space-y-6"
    >
      {/* File upload */}
      <div className="space-y-2">
        <label className="block text-sm font-semibold text-gray-700">
          {t("fileLabel")}
        </label>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="py-2 px-4 rounded-lg bg-red-50 text-red-700 text-sm font-medium hover:bg-red-100 transition-colors"
          >
            {t("fileSelectButton")}
          </button>
          <span className="text-sm text-gray-500">
            {files.length > 0
              ? t("fileSelected").replace("{count}", String(files.length))
              : ""}
          </span>
        </div>
        <input
          type="file"
          accept=".xlsx,.csv,.txt,.pdf,.png,.jpg,.jpeg"
          multiple
          ref={fileInputRef}
          onChange={handleFileChange}
          className="hidden"
        />
        {fileError && (
          <p className="text-sm text-red-600">{fileError}</p>
        )}

        {files.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {files.map((file, index) => (
              <li
                key={`${file.name}-${index}`}
                className="flex items-center justify-between py-2 px-3 bg-gray-50 border border-gray-200 rounded-lg text-sm"
              >
                <span className="text-gray-800 truncate mr-2">{file.name}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveFile(index)}
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

      {/* Optional PowerPoint template */}
      <div className="space-y-2">
        <label className="block text-sm font-semibold text-gray-700">
          {t("templateLabel")}
        </label>
        <p className="text-sm text-gray-500">{t("templateHint")}</p>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => templateInputRef.current?.click()}
            className="py-2 px-4 rounded-lg bg-red-50 text-red-700 text-sm font-medium hover:bg-red-100 transition-colors"
          >
            {t("templateSelectButton")}
          </button>
          <span className="text-sm text-gray-500">
            {template
              ? t("fileSelected").replace("{count}", "1")
              : ""}
          </span>
        </div>
        <input
          type="file"
          accept=".pptx"
          ref={templateInputRef}
          onChange={handleTemplateChange}
          className="hidden"
        />
        {templateError && (
          <p className="text-sm text-red-600">{templateError}</p>
        )}
        {template && (
          <ul className="mt-2 space-y-1.5">
            <li className="flex items-center justify-between py-2 px-3 bg-gray-50 border border-gray-200 rounded-lg text-sm">
              <span className="text-gray-800 truncate mr-2">
                {template.name}
              </span>
              <button
                type="button"
                onClick={() => setTemplate(null)}
                className="shrink-0 w-6 h-6 flex items-center justify-center rounded-full text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                aria-label={`${t("remove")} ${template.name}`}
              >
                ✕
              </button>
            </li>
          </ul>
        )}
      </div>

      {/* Prompt */}
      <div className="space-y-2">
        <label
          htmlFor="prompt"
          className="block text-sm font-semibold text-gray-700"
        >
          {t("promptLabel")}
        </label>
        <textarea
          id="prompt"
          rows={4}
          placeholder={t("promptPlaceholder")}
          {...register("prompt")}
          className="block w-full rounded-lg border border-gray-300 px-4 py-3 text-sm shadow-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none transition-all resize-vertical"
        />
        {errors.prompt && (
          <p className="text-sm text-red-600">{t("promptRequired")}</p>
        )}
      </div>

      {/* Submit */}
      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full py-3 px-6 rounded-lg bg-red-600 text-white font-semibold text-base shadow-md hover:bg-red-700 hover:shadow-lg disabled:bg-red-300 disabled:cursor-not-allowed transition-all duration-200"
      >
        {isSubmitting ? t("submitting") : t("submitButton")}
      </button>
    </form>
  );
}
