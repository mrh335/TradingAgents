// Shared formatters — display helpers used everywhere in the app.
//
// Date/time policy (2026-05-23+):
// * The backend stores all timestamps in UTC with a Z suffix (e.g.
//   "2026-05-23T06:14:00Z"). That's correct design — UTC is unambiguous.
// * The frontend ALWAYS renders in the browser's local timezone.
// * For "today" defaults (date pickers, trade_date defaults, etc.),
//   use ``todayLocalIso()`` — NOT ``new Date().toISOString().slice(0,10)``
//   which returns tomorrow's UTC date when the user is in a negative
//   offset zone like PT late at night.

export function fmtTokens(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString();
}

/** YYYY-MM-DD for the user's LOCAL today.
 *
 * Replaces the broken ``new Date().toISOString().slice(0,10)`` pattern
 * that returned tomorrow's date for users in negative-offset timezones
 * after ~5pm local. Use this for any "today" default in a date input or
 * a submission body.
 */
export function todayLocalIso(): string {
  const d = new Date();
  // Use the Sweden locale because it formats as YYYY-MM-DD natively, then
  // we're guaranteed the output is local-day-of-month regardless of TZ.
  return d.toLocaleDateString("sv-SE");
}

/** Same as todayLocalIso() but takes an existing Date and returns the
 * YYYY-MM-DD of the LOCAL day for it. Useful when shifting by days. */
export function localIsoDate(d: Date): string {
  return d.toLocaleDateString("sv-SE");
}

/** Full timestamp in local time: e.g. "May 23, 2026, 11:14 PM"
 *
 * Renders any ISO-with-Z UTC string (or Date) as the user's local time.
 * Returns "—" for null/empty input. Returns the input unchanged on
 * unparseable strings.
 */
export function fmtLocalTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Compact date+time: e.g. "5/23/26, 11:14 PM" — for tight columns. */
export function fmtLocalTimeShort(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "2-digit",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Local date only: e.g. "May 23, 2026"
 *
 * Use for date-stamps where the time component isn't meaningful
 * (e.g. trade_date, opened_at when it's just a date). Strips time
 * entirely.
 */
export function fmtLocalDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

/** Relative time: e.g. "2 hours ago", "in 3 days". For things that
 * benefit from a human-readable delta (last refreshed, next earnings).
 */
export function fmtRelative(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diffMs = d.getTime() - Date.now();
  const abs = Math.abs(diffMs);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  let value: number;
  let unit: Intl.RelativeTimeFormatUnit;
  if (abs < hour) {
    value = Math.round(diffMs / minute);
    unit = "minute";
  } else if (abs < day) {
    value = Math.round(diffMs / hour);
    unit = "hour";
  } else if (abs < 30 * day) {
    value = Math.round(diffMs / day);
    unit = "day";
  } else if (abs < 365 * day) {
    value = Math.round(diffMs / (30 * day));
    unit = "month";
  } else {
    value = Math.round(diffMs / (365 * day));
    unit = "year";
  }
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(value, unit);
}

/** Legacy alias — most existing code calls fmtDate(); now delegates to
 * the local-time helper instead of the old "strip Z + show UTC" bug.
 *
 * Keep this signature to avoid touching every caller in one go.
 */
export function fmtDate(iso?: string | null): string {
  return fmtLocalTimeShort(iso);
}

export function statusColor(status: string): string {
  switch (status) {
    case "done":
      return "bg-success/15 text-success";
    case "running":
      return "bg-accent/15 text-accent";
    case "error":
      return "bg-danger/15 text-danger";
    default:
      return "bg-muted/15 text-muted";
  }
}

export function decisionColor(decision?: string | null): string {
  if (!decision) return "text-muted";
  const d = decision.toUpperCase();
  if (d.includes("BUY") || d.includes("OVERWEIGHT")) return "text-success";
  if (d.includes("SELL") || d.includes("REDUCE") || d.includes("AVOID")) return "text-danger";
  return "text-warning";
}
