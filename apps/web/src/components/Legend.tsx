"use client";

import { useTranslations } from "next-intl";
import { lineColor, type Line } from "@/lib/api";

type Props = {
  lines: Line[];
  counts: Map<string, number>;
};

export default function Legend({ lines, counts }: Props) {
  const t = useTranslations("legend");

  return (
    <aside className="legend">
      <h2 className="legend__title">{t("lines")}</h2>
      <ul className="legend__list">
        {lines.map((line) => (
          <li key={line.id} className="legend__item">
            <span className="chip" style={{ backgroundColor: lineColor(line.color) }}>
              {line.id}
            </span>
            <span className="legend__count">{counts.get(line.id) ?? 0}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
