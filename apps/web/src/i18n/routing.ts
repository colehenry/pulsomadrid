import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  // Spanish first: this is a Madrid product, read mostly by people in Madrid.
  locales: ["es", "en"],
  defaultLocale: "es",

  // Spanish lives at "/", not "/es". English keeps its "/en" prefix so it stays a
  // shareable link and still prerenders as its own page. "/es" redirects to "/", so
  // there is exactly one URL per language.
  localePrefix: "as-needed",
});

export type Locale = (typeof routing.locales)[number];
