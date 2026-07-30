import type { JobStatusResponse } from "@/types";

interface JobProgressProps {
  job: JobStatusResponse;
}

const statusColors: Record<string, string> = {
  queued: "bg-indigo-100 text-indigo-700",
  running: "bg-amber-100 text-amber-700",
  succeeded: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

export function JobProgress({ job }: JobProgressProps) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-800">
          {job.stage.replace(/_/g, " ")}
        </span>
        <span
          className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase ${statusColors[job.status] ?? "bg-gray-100 text-gray-600"}`}
        >
          {job.status}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-red-600 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${job.progress}%` }}
        />
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{job.message}</span>
        <span className="font-mono">{job.progress}%</span>
      </div>
    </div>
  );
}
