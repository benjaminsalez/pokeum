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
  image_url: string | null;
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

/**
 * POST a captured frame to the recognizer.
 *
 * With `requireDetection`, the server answers `no_card_detected` when no card
 * quad is found. `guideMargin` instead lets a guided camera capture fall back
 * to its known inner card region when quad detection fails.
 */
export async function identify(
  blob: Blob,
  topK = 5,
  requireDetection = false,
  guideMargin?: number,
): Promise<IdentifyResponse> {
  const form = new FormData();
  form.append("file", blob, "frame.jpg");
  const query = new URLSearchParams({ top_k: String(topK) });
  if (requireDetection) query.set("require_detection", "true");
  if (guideMargin !== undefined) query.set("guide_margin", String(guideMargin));
  const response = await fetch(`${API_BASE}/identify?${query}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(`identify failed: HTTP ${response.status}`);
  }
  return (await response.json()) as IdentifyResponse;
}

/** Mirrors the backend's SCAN_ANNOTATION_SCHEMA_VERSION. */
export const SCAN_ANNOTATION_SCHEMA_VERSION = 1;

export interface ScanAnnotation {
  schema_version: number;
  consent: boolean;
  card_id: string;
  set_id: string;
  number: string;
  status: string;
  variants: VariantGuess[];
  alternate_card_ids: string[];
  captured_at: string;
}

/** Submit an accepted scan (photo + annotation) for background collection. */
export async function submitScan(blob: Blob, annotation: ScanAnnotation): Promise<void> {
  const form = new FormData();
  form.append("file", blob, "scan.jpg");
  form.append("annotation", JSON.stringify(annotation));
  const response = await fetch(`${API_BASE}/scans`, { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(`scan submit failed: HTTP ${response.status}`);
  }
}

/** URL of a card's reference image (served by the API from its cache). */
export function cardImageUrl(cardId: string): string {
  return `${API_BASE}/cards/${encodeURIComponent(cardId)}/image`;
}

/**
 * Best URL for a card's artwork: the TCGdex CDN when the catalogue knows it
 * (fast, cacheable offline, no server disk), else the API image endpoint —
 * which itself redirects to the CDN when the server has no local image cache.
 */
export function cardArtUrl(card: Pick<CandidateOut, "card_id" | "image_url">): string {
  return card.image_url ?? cardImageUrl(card.card_id);
}

/** Check the API is up and how many cards are indexed. */
export async function health(): Promise<{ status: string; cards_indexed: number }> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error(`health failed: HTTP ${response.status}`);
  }
  return (await response.json()) as { status: string; cards_indexed: number };
}
