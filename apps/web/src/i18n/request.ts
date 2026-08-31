import { hasLocale } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import { routing } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;

  return {
    locale,
    // Times are shown to a user standing in Madrid, so they are formatted in Madrid's
    // zone regardless of where the browser or the server happens to be.
    timeZone: "Europe/Madrid",
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
