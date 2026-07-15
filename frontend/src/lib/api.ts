/** Typed client for the pokeum recognizer API.

Defaults to same-origin `/api` (the Vite proxy). Set `VITE_API_BASE` to a full
URL (e.g. a tunneled API host) to call the recognizer cross-origin — the API
sends permissive CORS headers.
*/

const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export interface CardSet {
  id: string;
  name: string;
  code: string | null;
}

export interface VariantGuess {
  kind: string;
  present: boolean;
  confidence: number;
}

export interface CandidateOut {
  card_id: string;
  name: string;
  set: CardSet;
  number: string;
  rarity: string | null;
  confidence: number;
  signals: Record<string, number>;
  variants?: VariantGuess[];
}

export interface IdentifyResponse {
  status: "confident" | "uncertain" | "no_match" | "no_card_detected";
  match: CandidateOut | null;
  alternates: CandidateOut[];
  ocr: { number: string | null; number_total: number | null; set_code: string | null } | null;
}

/** POST a captured frame to the recognizer. */
export async function identify(blob: Blob, topK = 5): Promise<IdentifyResponse> {
  const form = new FormData();
  form.append("file", blob, "frame.jpg");
  const response = await fetch(`${API_BASE}/identify?top_k=${topK}`, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`identify failed: HTTP ${response.status}`);
  }
  return (await response.json()) as IdentifyResponse;
}

/** URL of a card's reference image (served by the API from its cache). */
export function cardImageUrl(cardId: string): string {
  return `${API_BASE}/cards/${encodeURIComponent(cardId)}/image`;
}

/** Check the API is up and how many cards are indexed. */
export async function health(): Promise<{ status: string; cards_indexed: number }> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error(`health failed: HTTP ${response.status}`);
  }
  return (await response.json()) as { status: string; cards_indexed: number };
}
