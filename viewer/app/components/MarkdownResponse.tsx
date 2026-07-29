import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownResponseProps = {
  children: string;
};

export function MarkdownResponse({ children }: MarkdownResponseProps) {
  return (
    <div className="response-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
