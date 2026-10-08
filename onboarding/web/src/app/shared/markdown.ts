/**
 * Minimal, safe Markdown for chat messages: fenced code blocks (with a Copy button), paragraphs, bullet lists, **bold**
 * and `code`. Produces plain data rendered by templates; no innerHTML, so nothing in a message can inject markup.
 */
export type Inline = { kind: 'text' | 'bold' | 'code'; text: string };
export type Block =
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'para'; parts: Inline[] }
  | { kind: 'list'; items: Inline[][] }
  | { kind: 'heading'; parts: Inline[] };

export function inline(text: string): Inline[] {
  const out: Inline[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index! > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    const token = m[0];
    out.push(token.startsWith('**') ? { kind: 'bold', text: token.slice(2, -2) } : { kind: 'code', text: token.slice(1, -1) });
    last = m.index! + token.length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

export function parse(markdown: string): Block[] {
  const blocks: Block[] = [];
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  let para: string[] = [];
  let list: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: 'para', parts: inline(para.join(' ')) });
    if (list.length) blocks.push({ kind: 'list', items: list.map(inline) });
    para = [];
    list = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w-]*)\s*$/);
    if (fence) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
      blocks.push({ kind: 'code', lang: fence[1] || '', text: body.join('\n') });
      continue;
    }
    const bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) {
      flush();
      blocks.push({ kind: 'heading', parts: inline(heading[1]) });
    } else if (bullet) {
      if (para.length) flush();
      list.push(bullet[1]);
    } else if (!line.trim()) {
      flush();
    } else {
      if (list.length) flush();
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}
