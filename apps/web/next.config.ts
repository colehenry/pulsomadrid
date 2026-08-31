import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {};

// Wired before the first component was written. Retrofitting i18n means touching
// every string in the app; the plan is Spanish-first with an English toggle.
const withNextIntl = createNextIntlPlugin();

export default withNextIntl(nextConfig);
