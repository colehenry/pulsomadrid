"use client";

import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "@/i18n/navigation";
import { routing, type Locale } from "@/i18n/routing";

/** Spanish first, English as a toggle. The URL carries the locale: /es, /en. */
export default function LocaleSwitcher() {
  const t = useTranslations("locale");
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();

  return (
    <div className="locales" aria-label={t("label")}>
      {routing.locales.map((option: Locale) => (
        <button
          key={option}
          type="button"
          className={option === locale ? "locales__item locales__item--on" : "locales__item"}
          aria-current={option === locale}
          onClick={() => router.replace(pathname, { locale: option })}
        >
          {t(option)}
        </button>
      ))}
    </div>
  );
}
