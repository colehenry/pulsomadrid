import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  // Spanish first: this is a Madrid product, read mostly by people in Madrid.
  locales: ["es", "en"],
  defaultLocale: "es",
});

export type Locale = (typeof routing.locales)[number];
