import { readFile, writeFile } from "node:fs/promises";

// Keep the detailed Vietnamese instructions in the repository guide.
const guide = await readFile(new URL("../../docs/USER_GUIDE.md", import.meta.url), "utf8");
const htmlRepo = "https://github.com/hungdinhxuan/Ludivoxa/blob/main/";
const headings = [...guide.matchAll(/^#{2,3} (.+)$/gm)];
const sections = new Map(
  headings.map((heading, i) => [
    heading[1].trim(),
    guide.slice(heading.index + heading[0].length, headings[i + 1]?.index).trim(),
  ]),
);

const pages = [
  {
    slug: "install",
    title: "Cài Ludivoxa offline trên Windows",
    description: "Tải bộ cài Windows x64 có sẵn Ollama, OCR và TTS. Cập nhật ứng dụng bằng gói vá nhỏ khi phù hợp.",
    sections: ["Cài bản Windows offline", "Cập nhật ứng dụng"],
  },
  {
    slug: "translate",
    title: "Dịch game tiếng Anh và tiếng Nhật",
    description: "Chọn cửa sổ game, khoanh ô OCR và dùng History hoặc Game profile để quản lý hội thoại.",
    sections: ["Dịch hội thoại", "Ngôn ngữ và engine", "Lịch sử hội thoại", "Hồ sơ theo game"],
  },
  {
    slug: "ollama",
    title: "Cấu hình Ollama local hoặc máy chủ LAN",
    description: "Dùng Ollama đi kèm Ludivoxa, Ollama riêng hoặc máy chủ tương thích OpenAI trên mạng LAN.",
    sections: ["Ollama local và GPU server LAN"],
  },
  {
    slug: "voice",
    title: "Phụ đề và giọng đọc tiếng Việt",
    description: "Chọn chế độ overlay, chỉnh âm lượng, nghe thử TTS, clone từ mẫu local hoặc gán giọng theo nhân vật.",
    sections: ["Overlay và giọng đọc", "Giọng theo nhân vật (thử nghiệm từ v1.2.0)"],
  },
  {
    slug: "troubleshooting",
    title: "Sửa lỗi OCR, âm thanh và Remote Desktop",
    description: "Kiểm tra bản xem trước, trạng thái capture, thiết bị âm thanh và kết nối khi dùng Ludivoxa qua RDP.",
    sections: ["Chẩn đoán capture và âm thanh", "Remote Desktop trên Windows", "Xử lý sự cố"],
  },
];

for (const { slug, title, description, sections: names } of pages) {
  const bodies = names.map((name) => {
    const text = sections.get(name);
    if (!text) throw new Error(`Missing guide section: ${name}`);
    return [name, text];
  });
  const content = bodies
    .map(([name, text], i) => (i === 0 ? text : `## ${name}\n\n${text}`))
    .join("\n\n")
    .replace(/\]\(([^:)#\s]+\.md)(#[^)]+)?\)/g, (_link, file, fragment = "") => {
      const path = new URL(file, `${htmlRepo}docs/USER_GUIDE.md`).href;
      return `](${path}${fragment})`;
    });
  const frontmatter = `---\ntitle: ${JSON.stringify(title)}\ndescription: ${JSON.stringify(description)}\n---\n\n`;
  await writeFile(new URL(`../src/content/docs/${slug}.md`, import.meta.url), `${frontmatter}${content}\n`);
}
