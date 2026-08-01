import { useEffect, useMemo, useState } from "react";

interface SourcePreviewProps {
  file?: File;
  filename: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SourcePreview({ file, filename }: SourcePreviewProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setObjectUrl(null);
      return;
    }

    const url = URL.createObjectURL(file);
    setObjectUrl(url);

    return () => URL.revokeObjectURL(url);
  }, [file]);

  const extension = useMemo(
    () => filename.split(".").pop()?.toLowerCase() ?? "",
    [filename]
  );

  const isImage = ["png", "jpg", "jpeg", "webp"].includes(extension);
  const isPdf = extension === "pdf";

  return (
    <div className="h-full min-h-[460px] rounded-2xl border border-gray-200 bg-gray-50 overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-5 py-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            原始來源
          </p>
          <p className="mt-1 truncate text-sm font-semibold text-gray-800">
            {filename}
          </p>
        </div>
        {file && (
          <span className="ml-3 shrink-0 rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-500">
            {formatBytes(file.size)}
          </span>
        )}
      </div>

      <div className="flex min-h-[395px] items-center justify-center p-4">
        {file && objectUrl && isImage && (
          <img
            src={objectUrl}
            alt={filename}
            className="max-h-[620px] max-w-full rounded-xl border border-gray-200 bg-white object-contain shadow-sm"
          />
        )}

        {file && objectUrl && isPdf && (
          <iframe
            src={objectUrl}
            title={filename}
            className="h-[620px] w-full rounded-xl border border-gray-200 bg-white"
          />
        )}

        {(!file || (!isImage && !isPdf)) && (
          <div className="max-w-sm text-center">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-2xl shadow-sm ring-1 ring-gray-200">
              📄
            </div>
            <p className="font-semibold text-gray-800">{filename}</p>
            <p className="mt-2 text-sm leading-6 text-gray-500">
              此格式無法直接在瀏覽器預覽。請以右側抽取結果、來源位置與信心分數進行核對。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
