import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { AssistantPanel, type AssistantTab } from "./assistant";
import { Icon } from "./Icon";
import { CharactersPage } from "./pages";
import { Modal } from "./shared";
import { useStudio, type Character, type Post } from "./store";

// Dock flutuante, igual em todas as telas: o Assistente (bolinha que abre a
// barra lateral, com conversa, agenda e planejamento) e as personagens em
// bolinhas logo acima dele. Fica montado o tempo todo: trocar de tela ou
// minimizar nao perde a conversa nem a resposta que esta chegando.

const TAB_KEY = "luna-assistente-aba";
const OPEN_KEY = "luna-assistente-aberto";
const COLORS = ["#8b5cf6", "#ec4899", "#38bdf8", "#34d399", "#f59e0b", "#f43f5e"];

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // sem armazenamento: so nao lembra
  }
}
function recall(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function avatarOf(c: Character): string | null {
  return /luna/i.test(c.name) ? "/login/avatar-luna.webp" : null;
}

interface Props {
  context: string;
  onOpenPost: (post: Post) => void;
  onNewPost: (date?: Date) => void;
  go: (page: string) => void;
  // outra tela pede para abrir o assistente (ex.: "Pedir ao assistente")
  request: { text: string; key: number } | null;
}

export function Dock({ context, onOpenPost, onNewPost, go, request }: Props) {
  const { characters, items } = useStudio();
  const [open, setOpen] = useState(() => recall(OPEN_KEY) === "1");
  const [tab, setTab] = useState<AssistantTab>(() => {
    const t = recall(TAB_KEY);
    return t === "agenda" || t === "plano" ? t : "conversa";
  });
  const [draft, setDraft] = useState<string | null>(null);
  const [unread, setUnread] = useState(false);
  const [person, setPerson] = useState<Character | null>(null);
  const [managing, setManaging] = useState(false);

  const show = useCallback((value: boolean) => {
    setOpen(value);
    remember(OPEN_KEY, value ? "1" : "0");
    if (value) setUnread(false);
  }, []);
  const pickTab = (t: AssistantTab) => {
    setTab(t);
    remember(TAB_KEY, t);
  };
  const ask = (text: string) => {
    pickTab("conversa");
    setDraft(text);
    show(true);
  };

  useEffect(() => {
    if (request) ask(request.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && open && !document.querySelector(".st-modal-backdrop")) show(false);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        show(!open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show]);

  const people = characters.filter((c) => c.active).slice(0, 5);
  const onDraftUsed = useCallback(() => setDraft(null), []);

  return (
    <>
      <aside className={open ? "st-drawer open" : "st-drawer"} aria-label="Assistente" aria-hidden={!open}>
        <header className="st-drawer-head">
          <img src="/login/avatar-luna.webp" alt="" />
          <span>
            <b>Assistente</b>
            <small>conversa, agenda e planejamento</small>
          </span>
          <button type="button" className="st-icon-btn" onClick={() => show(false)} aria-label="Minimizar assistente" title="Minimizar (Esc)">
            <Icon name="chevronRight" size={18} />
          </button>
        </header>
        <AssistantPanel
          context={context}
          tab={tab}
          onTab={pickTab}
          draft={draft}
          onDraftUsed={onDraftUsed}
          onReply={() => !open && setUnread(true)}
          onOpenPost={onOpenPost}
          onNewPost={onNewPost}
          onFullCalendar={() => go("calendario")}
        />
      </aside>

      <div className={open ? "st-dock drawer-open" : "st-dock"}>
        {people.map((c, i) => {
          const img = avatarOf(c);
          return (
            <button
              key={c.id}
              type="button"
              className={person?.id === c.id ? "st-dock-dot active" : "st-dock-dot"}
              style={{ "--dot": COLORS[i % COLORS.length] } as CSSProperties}
              onClick={() => setPerson(person?.id === c.id ? null : c)}
              aria-label={`Personagem ${c.name}`}
              title={c.name}
            >
              {img ? <img src={img} alt="" /> : c.name.slice(0, 1).toUpperCase()}
            </button>
          );
        })}
        <button type="button" className="st-dock-dot st-dock-add" onClick={() => setManaging(true)} aria-label="Personagens" title="Personagens">
          <Icon name={people.length ? "users" : "plus"} size={16} />
        </button>
        <button
          type="button"
          className={open ? "st-dock-main open" : "st-dock-main"}
          onClick={() => show(!open)}
          aria-label={open ? "Minimizar assistente" : "Abrir assistente"}
          aria-expanded={open}
          title="Assistente (Ctrl J)"
        >
          <Icon name={open ? "close" : "comment"} size={22} />
          {unread && <i className="st-dock-unread" aria-label="Resposta nova" />}
        </button>

        {person && (
          <div className="st-dock-card" role="dialog" aria-label={person.name}>
            <header>
              <b>{person.name}</b>
              <button type="button" className="st-icon-btn" onClick={() => setPerson(null)} aria-label="Fechar">
                <Icon name="close" size={14} />
              </button>
            </header>
            <p>{person.description || "Sem descrição."}</p>
            <small>{items.filter((it) => it.characterId === person.id).length} conteúdo(s) na biblioteca</small>
            <div className="st-dock-card-actions">
              <button
                type="button"
                className="st-btn st-btn-small st-btn-primary"
                onClick={() => {
                  ask(`Sobre a personagem ${person.name}: `);
                  setPerson(null);
                }}
              >
                <Icon name="comment" size={14} />
                Pedir ao assistente
              </button>
              <button
                type="button"
                className="st-btn st-btn-small"
                onClick={() => {
                  go("conteudos");
                  setPerson(null);
                }}
              >
                Biblioteca
              </button>
              <button
                type="button"
                className="st-btn st-btn-small"
                onClick={() => {
                  setManaging(true);
                  setPerson(null);
                }}
              >
                Editar
              </button>
            </div>
          </div>
        )}
      </div>

      {managing && (
        <Modal title="Personagens" onClose={() => setManaging(false)} wide>
          <CharactersPage />
        </Modal>
      )}
    </>
  );
}
