import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import MapView from "@/components/MapView";

type Props = { params: Promise<{ locale: string }> };

export default function HomePage({ params }: Props) {
  const { locale } = use(params);
  setRequestLocale(locale);

  return <MapView />;
}
