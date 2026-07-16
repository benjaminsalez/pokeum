/** First-visit data-collection notice: shown once, informational only.

Saved scans (the captured photo and its match) are collected to improve
recognition. This module only remembers that the notice was shown; it does not
gate anything. Bump `NOTICE_VERSION` when the text materially changes to show
it again.
*/

/** Bump when the notice wording materially changes to show it again. */
export const NOTICE_VERSION = 1;

const STORAGE_KEY = "pokeum.notice";

/** Whether the current version of the notice has been acknowledged. */
export function hasSeenNotice(): boolean {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    return (JSON.parse(raw) as { version: number }).version === NOTICE_VERSION;
  } catch {
    return false;
  }
}

/** Remember the notice was shown; storage failures (private mode) are ignored. */
export function markNoticeSeen(): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: NOTICE_VERSION, timestamp: new Date().toISOString() }),
    );
  } catch {
    // Blocked storage: the notice will simply re-appear next visit.
  }
}
