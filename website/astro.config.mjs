import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

const pages = new URL(process.env.PAGES_URL || "https://hungdinhxuan.github.io/Ludivoxa/");
pages.protocol = "https:";

export default defineConfig({
  site: pages.origin,
  base: pages.pathname,
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "Ludivoxa",
      logo: { src: "./src/assets/ludivoxa.png" },
      favicon: "/ludivoxa.png",
      customCss: ["./src/styles/brand.css"],
      defaultLocale: "root",
      locales: {
        root: { label: "Tiếng Việt", lang: "vi" },
        en: { label: "English", lang: "en" },
      },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/hungdinhxuan/Ludivoxa",
        },
      ],
      sidebar: [
        {
          label: "Bắt đầu",
          translations: { en: "Get started" },
          items: ["install", "translate"],
        },
        {
          label: "Cấu hình",
          translations: { en: "Configuration" },
          items: ["ollama", "voice"],
        },
        {
          label: "Tham khảo",
          translations: { en: "Reference" },
          items: ["troubleshooting", "benchmarks"],
        },
      ],
    }),
  ],
});
