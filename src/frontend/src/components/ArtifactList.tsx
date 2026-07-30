import type { Artifact } from "@/types";
import { useI18n } from "@/i18n";

interface ArtifactListProps {
  artifacts: Artifact[];
}

export function ArtifactList({ artifacts }: ArtifactListProps) {
  const { t } = useI18n();

  if (artifacts.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <h3 className="text-lg font-bold text-gray-900">{t("artifactTitle")}</h3>
      <ul className="space-y-2">
        {artifacts.map((artifact) => (
          <li
            key={artifact.filename}
            className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-xl shadow-sm"
          >
            <div className="w-9 h-9 rounded-lg bg-red-50 flex items-center justify-center text-red-600 text-xs font-bold uppercase">
              {artifact.type}
            </div>
            <span className="flex-1 text-sm text-gray-800 font-medium">
              {artifact.filename}
            </span>
            <a
              href={artifact.download_url}
              download={artifact.filename}
              className="px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-semibold shadow hover:bg-red-700 hover:shadow-md transition-all duration-200"
            >
              {t("artifactDownload")}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
