"use client";

import { useTranslations } from "next-intl";
import { lineColor, type Line, type Vehicle } from "@/lib/api";

type Props = {
  vehicle: Vehicle;
  line: Line | undefined;
  onClose: () => void;
};

export default function VehicleCard({ vehicle, line, onClose }: Props) {
  const t = useTranslations("train");
  const where = vehicle.at_station ?? "—";

  return (
    <aside className="card">
      <button type="button" className="card__close" onClick={onClose} aria-label="×">
        ×
      </button>
      <p className="card__line">
        <span className="chip" style={{ backgroundColor: lineColor(line?.color) }}>
          {vehicle.line_id}
        </span>
        {t("number", { number: vehicle.train_number })}
      </p>
      <p className="card__where">{t(vehicle.status, { station: where })}</p>
      {vehicle.destination && (
        <p className="card__towards">{t("destination", { station: vehicle.destination })}</p>
      )}
      {/* Only when it says something new: a short-turn train terminates short of the
          terminus its direction heads for. */}
      {vehicle.towards && vehicle.towards !== vehicle.destination && (
        <p className="card__towards">{t("towards", { station: vehicle.towards })}</p>
      )}
      <p className="card__calls">{t("calls", { count: vehicle.calls_at })}</p>
    </aside>
  );
}
