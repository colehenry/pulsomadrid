"use client";

import { useFormatter, useTranslations } from "next-intl";

type Props = {
  count: number;
  observedAt: string | null;
  upstreamOk: boolean;
  ageSeconds: number | null;
};

/** How old the feed may be before the page says so. Two missed polls. */
const STALE_AFTER_SECONDS = 90;

export default function StatusPill({ count, observedAt, upstreamOk, ageSeconds }: Props) {
  const t = useTranslations("status");
  const format = useFormatter();

  if (!observedAt) {
    return <p className="status status--warn">{t("waiting")}</p>;
  }

  const stale = ageSeconds !== null && ageSeconds > STALE_AFTER_SECONDS;
  const detail = !upstreamOk
    ? t("unreachable")
    : stale
      ? t("stale", { minutes: Math.max(1, Math.round((ageSeconds ?? 0) / 60)) })
      : t("updated", {
          time: format.dateTime(new Date(observedAt), {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
        });

  return (
    <p className={stale || !upstreamOk ? "status status--warn" : "status"}>
      <span className="status__count">{t("trains", { count })}</span>
      <span className="status__detail">{detail}</span>
    </p>
  );
}
