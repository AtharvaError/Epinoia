import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { type KeyboardEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Dialog, useDebounced } from "./ui";
import { api, type SearchResult } from "../lib/api";

export interface Command { label: string; hint: string; to?: string; run?: () => void }

/** Ctrl+K: jump to a page, start a simulation, or find a participant, event, equipment or request. */
export function CommandPalette({ festivalId, commands, onClose }: { festivalId: number; commands: Command[]; onClose: () => void }) {
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const [index, setIndex] = useState(0);
  const query = useDebounced(term.trim());
  const results = useQuery({
    queryKey: ["search", festivalId, query],
    queryFn: () => api<SearchResult[]>(`/api/festivals/${festivalId}/search?q=${encodeURIComponent(query)}`),
    enabled: query.length >= 2,
  });
  const needle = term.trim().toLowerCase();
  const found: Command[] = (query.length >= 2 ? results.data ?? [] : []).map((r) => {
    let to = `/${r.type === "Event" ? "events" : "participants"}?open=${r.code}`;
    if (r.type === "Equipment") {
      to = `/transportation?tab=stock&search=${encodeURIComponent(r.title)}`;
    } else if (r.type === "Equipment Request") {
      to = `/transportation?tab=equipment&open=${encodeURIComponent(r.code)}`;
    } else if (r.type === "Merchandise") {
      to = `/merchandise?search=${encodeURIComponent(r.title)}`;
    }
    return {
      label: r.title,
      hint: `${r.type} ${r.code}, ${r.subtitle}`,
      to,
    };
  });
  const options = [...commands.filter((c) => c.label.toLowerCase().includes(needle)), ...found].slice(0, 12);
  const selected = Math.min(index, Math.max(options.length - 1, 0));

  const choose = (option: Command) => {
    onClose();
    if (option.run) option.run();
    else navigate(option.to!);
  };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((selected + (e.key === "ArrowDown" ? 1 : -1) + options.length) % (options.length || 1));
    }
    if (e.key === "Enter" && options[selected]) choose(options[selected]);
  };

  return (
    <Dialog title="Search or jump to" onClose={onClose} className="palette">
      <div className="search palette-input">
        <Search size={16} aria-hidden="true" />
        <input data-autofocus role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={options.length ? `palette-${selected}` : undefined}
               aria-label="Search pages, simulations, participants and events" placeholder="Page, simulation, participant or event" value={term}
               onChange={(e) => { setTerm(e.target.value); setIndex(0); }} onKeyDown={keys} />
      </div>
      <ul id="palette-list" role="listbox" aria-label="Results" className="palette-list">
        {options.map((option, i) => (
          // The input keeps focus and handles the keyboard, so these rows only need a pointer handler.
          <li key={option.label + option.hint} id={`palette-${i}`} role="option" aria-selected={i === selected} className={i === selected ? "active" : ""}
              onMouseEnter={() => setIndex(i)} onClick={() => choose(option)}>
            <strong>{option.label}</strong><small>{option.hint}</small>
          </li>
        ))}
        {!options.length && <li className="muted small">{results.isPending && query.length >= 2 ? "Searching" : `No matches for “${term}”.`}</li>}
      </ul>
      {results.isError && <p className="form-error">{results.error.message}</p>}
    </Dialog>
  );
}
