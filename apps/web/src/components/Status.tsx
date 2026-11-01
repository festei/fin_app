import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status">
      {what}…
    </p>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : null;
  const text =
    status === 503
      ? "The API has no database right now."
      : error instanceof Error
        ? error.message
        : "Something went wrong.";
  return (
    <p className="error" role="alert">
      {text}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
