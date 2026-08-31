// Next 16 renamed this convention from `middleware.ts` to `proxy.ts`; next-intl's
// factory is unchanged. It resolves the locale from the URL prefix, then the cookie,
// then Accept-Language, and redirects a bare "/" to the default locale.
import createMiddleware from "next-intl/middleware";
import { routing } from "@/i18n/routing";

export default createMiddleware(routing);

export const config = {
  // Everything except Next internals and files with an extension.
  matcher: "/((?!api|_next|_vercel|.*\\..*).*)",
};
