let authorization = "";
export const base = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
export function setCredentials(user: string, password: string) {
  authorization = "Basic " + btoa(unescape(encodeURIComponent(user + ":" + password)));
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(base + path, { ...options, credentials: "include",
      headers: { ...(authorization ? { Authorization: authorization } : {}), ...options.headers } });
  } catch { throw new Error("Cannot reach the backend. Check that it is running and allows this frontend origin."); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(response.status === 401 ? "Incorrect username or password." :
      typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}
export async function evidenceImage(path: string): Promise<Blob> {
  const response = await fetch(base + path, { credentials: "include", headers: authorization ? { Authorization: authorization } : {} });
  if (!response.ok) throw new Error(`Image request failed (${response.status})`);
  return response.blob();
}
