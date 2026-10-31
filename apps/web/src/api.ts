export type Features = Record<string, boolean>;

export async function fetchFeatures(): Promise<Features> {
  const res = await fetch("/api/features");
  if (!res.ok) throw new Error(`features request failed: ${res.status}`);
  return res.json();
}
