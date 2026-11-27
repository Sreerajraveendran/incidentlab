import { useEffect, useState } from "react";

// The API token (API_TOKEN in secrets.env) that writes, load changes and
// incident commands need. Shared by every view and kept in this browser only.
export const TOKEN_KEY = "incidentlab.apiToken";

export function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return ""; // storage unavailable (private window, blocked site data)
  }
}

export function useToken(): [string, (token: string) => void] {
  const [token, setToken] = useState(readToken);
  useEffect(() => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: the token lives for this page only */
    }
  }, [token]);
  return [token, setToken];
}
