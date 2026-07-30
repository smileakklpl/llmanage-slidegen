import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { GeneratePage } from "@/pages/GeneratePage";
import { JobPage } from "@/pages/JobPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      {
        path: "/",
        element: <GeneratePage />,
      },
      {
        path: "/jobs/:jobId",
        element: <JobPage />,
      },
      {
        path: "*",
        element: <NotFoundPage />,
      },
    ],
  },
]);
