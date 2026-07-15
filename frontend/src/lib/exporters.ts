/** Export scanned cards to formats collectors import elsewhere. */

import type { CandidateOut } from "./api";

export interface ScanEntry {
  card: CandidateOut;
  quantity: number;
  scannedAt: string;
}

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * TCGplayer-style collection CSV. The column set (Quantity, Name, Set,
 * Card Number, Condition, Language, Printing) is the de-facto interchange
 * format most collection trackers (TCGplayer app, Collectr, Dragon Shield
 * imports) can map directly.
 */
export function toTcgplayerCsv(entries: ScanEntry[]): string {
  const header = ["Quantity", "Name", "Set", "Card Number", "Condition", "Language", "Printing"];
  const lines = [header.join(",")];
  for (const entry of entries) {
    const printing = entry.card.variants?.some((v) => v.kind === "reverse_holo" && v.present)
      ? "Reverse Holofoil"
      : "Normal";
    lines.push(
      [
        String(entry.quantity),
        csvEscape(entry.card.name),
        csvEscape(entry.card.set.name),
        csvEscape(entry.card.number),
        "Near Mint",
        "English",
        printing,
      ].join(","),
    );
  }
  return lines.join("\n") + "\n";
}

/** Plain-text list ("1 Pikachu PAL 025/193") for pasting into chats/forums. */
export function toPlainList(entries: ScanEntry[]): string {
  return entries
    .map((entry) => {
      const code = entry.card.set.code ? ` ${entry.card.set.code}` : "";
      return `${entry.quantity} ${entry.card.name}${code} ${entry.card.number}`;
    })
    .join("\n");
}

/** Trigger a client-side file download. */
export function downloadFile(filename: string, content: string, mime = "text/csv"): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
