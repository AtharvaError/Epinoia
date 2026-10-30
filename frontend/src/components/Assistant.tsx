import { useMutation, useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Dialog, ErrorNote } from "./ui";
import { api } from "../lib/api";

interface Reply { answer: string; mode: "claude" | "offline"; tools: string[]; note?: string }
interface Message { role: "user" | "assistant"; text: string; tools?: string[]; note?: string }

const STARTERS = [
  "How many participants have not received accommodation?", "Which events have venue conflicts?", "Which volunteers are available tomorrow?",
  "Which events could be affected by a transport delay?", "What is our remaining approved budget?", "What happens if we move Chess to LT-1?", "Which departments have pending tasks?",
];

export function Assistant({ festivalId, onClose, messages, setMessages }: { festivalId: number; onClose: () => void; messages: Message[]; setMessages: (m: Message[]) => void }) {
  const [text, setText] = useState("");
  const path = `/api/festivals/${festivalId}/assistant`;
  const end = useRef<HTMLDivElement>(null);
  const status = useQuery({ queryKey: ["assistant", festivalId], queryFn: () => api<{ mode: string; topics: string[] }>(path) });
  const ask = useMutation({
    mutationFn: (question: string) => api<Reply>(path, { method: "POST", body: JSON.stringify({ question, history: messages.slice(-10).map(({ role, text: t }) => ({ role, text: t })) }) }),
    onSuccess: (reply, question) => setMessages([...messages, { role: "user", text: question }, { role: "assistant", text: reply.answer, tools: reply.tools, note: reply.note }]),
  });
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, ask.isPending]);
  const send = (question: string) => {
    const q = question.trim();
    if (q.length < 2 || ask.isPending) return;
    setText("");
    ask.mutate(q);
  };
  return (
    <Dialog title="Festival assistant" onClose={onClose} side>
      <div className="assistant-panel">
        <p className="muted small">
          {status.data?.mode === "claude" ? "Answers from festival data your role can read." : "Offline mode: answers set questions from festival data your role can read. Add an API key for free-form questions."} It can't change anything.
        </p>
        <div className="assistant-log" aria-live="polite">
          {messages.length === 0 && <div className="chips">{STARTERS.map((q) => <button key={q} className="chip" disabled={ask.isPending} onClick={() => send(q)}>{q}</button>)}</div>}
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              <p>{m.text}</p>
              {m.tools && m.tools.length > 0 && <small>Checked: {m.tools.join(", ")}</small>}
              {m.note && <small>{m.note}</small>}
            </div>
          ))}
          {ask.isPending && <div className="bubble user pending"><p>{ask.variables}</p></div>}
          {ask.isPending && <p className="muted small" role="status">Checking festival data</p>}
          <ErrorNote error={ask.error} />
          <div ref={end} />
        </div>
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input aria-label="Ask the assistant" placeholder="Ask about the festival" maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />
          <button className="icon-button" aria-label="Send question" disabled={ask.isPending || text.trim().length < 2}><Send size={16} /></button>
        </form>
      </div>
    </Dialog>
  );
}
export type { Message };
