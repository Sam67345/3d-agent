// 极简 markdown 渲染：只支持 AI 回复里实际用到的语法
// （**粗体**、`代码`、## 标题、- 列表、换行）。
// 刻意不引第三方 markdown 库，保持依赖不变。
import type { ReactNode } from "react";

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  // 依次匹配 **粗体** 和 `代码`
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      out.push(
        <strong key={`${keyBase}-b${i++}`} className="font-semibold">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      out.push(
        <code
          key={`${keyBase}-c${i++}`}
          className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[12px] text-slate-700"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];

  const flushList = (key: string) => {
    if (!list.length) return;
    blocks.push(
      <ul key={key} className="my-1 ml-4 list-disc space-y-0.5 text-[13px]">
        {list.map((it, i) => (
          <li key={i}>{inline(it, `${key}-${i}`)}</li>
        ))}
      </ul>,
    );
    list = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    if (/^\s*[-*]\s+/.test(line)) {
      list.push(line.replace(/^\s*[-*]\s+/, ""));
      return;
    }
    flushList(`ul-${idx}`);
    if (!line.trim()) return;
    if (line.startsWith("### ")) {
      blocks.push(
        <h3 key={idx} className="mb-1 mt-2 text-[13px] font-semibold text-slate-800">
          {inline(line.slice(4), `h-${idx}`)}
        </h3>,
      );
    } else if (line.startsWith("## ")) {
      blocks.push(
        <h2 key={idx} className="mb-1 mt-2 text-[14px] font-semibold text-slate-900">
          {inline(line.slice(3), `h-${idx}`)}
        </h2>,
      );
    } else {
      blocks.push(
        <p key={idx} className="my-0.5 text-[13px] leading-relaxed">
          {inline(line, `p-${idx}`)}
        </p>,
      );
    }
  });
  flushList("ul-end");

  return <div className="whitespace-pre-wrap break-words">{blocks}</div>;
}