import { useRef, useState } from "react";
import type { ChatMessage, PersonaSummary } from "../api/client";
import { sendChatMessage } from "../api/client";

interface ChatEntry extends ChatMessage {
  model?: string;
}

// Estado da conversa fica no App (via este hook) e nao dentro do painel:
// assim fechar o assistente ou trocar de aba nao apaga o historico, e uma
// resposta que chegue com o painel fechado nao se perde.
export function useChatSession() {
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Trava sincrona contra envio duplicado (dois Enter antes do re-render).
  const sendingRef = useRef(false);

  // Retorna false se a mensagem nao foi enviada/respondida, para o chamador
  // devolver o texto ao campo e o usuario poder tentar de novo.
  async function send(personaId: string, text: string): Promise<boolean> {
    if (sendingRef.current) return false;
    sendingRef.current = true;

    const history: ChatMessage[] = messages.map(({ role, content }) => ({ role, content }));
    const next: ChatMessage[] = [...history, { role: "user", content: text }];
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);
    setError(null);

    try {
      const reply = await sendChatMessage(personaId || undefined, next);
      setMessages((prev) => [...prev, { role: "assistant", content: reply.content, model: reply.model }]);
      return true;
    } catch (e) {
      // Tira a mensagem sem resposta do historico para nao mandar duas
      // mensagens "user" seguidas na proxima tentativa.
      setMessages((prev) => prev.slice(0, -1));
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      sendingRef.current = false;
      setLoading(false);
    }
  }

  function clear() {
    if (sendingRef.current) return;
    setMessages([]);
    setError(null);
  }

  return { messages, loading, error, send, clear };
}

export type ChatSession = ReturnType<typeof useChatSession>;

interface Props {
  chat: ChatSession;
  personas: PersonaSummary[];
  personaId: string;
  onUsePrompt: (prompt: string) => void;
}

// Se a resposta do assistente tiver uma linha "PROMPT: ...", extrai so o
// texto do prompt para o botao "Usar este prompt" nao levar a conversa
// inteira para o campo de geracao.
function extractPrompt(content: string): string | null {
  const match = content.match(/PROMPT:\s*(.+)/i);
  return match ? match[1].trim() : null;
}

export function ChatAssistant({ chat, personas, personaId, onUsePrompt }: Props) {
  const [input, setInput] = useState("");
  const { messages, loading, error } = chat;

  const personaName = personas.find((p) => p.id === personaId)?.name;

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    const ok = await chat.send(personaId, text);
    if (!ok) setInput((current) => current || text);
  }

  return (
    <div className="panel chat-assistant">
      <h2>Assistente de prompt</h2>
      <p className="muted small">
        {personaName
          ? `Conversando sobre a persona ${personaName}. As caracteristicas fixas dela nao precisam ser descritas.`
          : "Descreva a cena que voce quer gerar - o assistente ajuda a montar o prompt."}
      </p>

      <div className="chat-messages">
        {messages.length === 0 && <p className="muted small">Nenhuma mensagem ainda.</p>}
        {messages.map((m, i) => {
          const extracted = m.role === "assistant" ? extractPrompt(m.content) : null;
          return (
            <div key={i} className={`chat-bubble ${m.role}`}>
              <p>{m.content}</p>
              {extracted && (
                <button type="button" className="small" onClick={() => onUsePrompt(extracted)}>
                  Usar este prompt
                </button>
              )}
            </div>
          );
        })}
        {loading && <p className="muted small">Gerando resposta...</p>}
      </div>

      {error && <p className="error small">{error}</p>}

      <div className="chat-input-row">
        <textarea
          rows={2}
          value={input}
          placeholder="Ex: quero ela sentada num cafe pela manha..."
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <button type="button" onClick={handleSend} disabled={loading || !input.trim()}>
          {loading ? "Gerando..." : "Enviar"}
        </button>
      </div>
      {messages.length > 0 && (
        <p className="muted small">
          {messages.at(-1)?.model ? `Modelo: ${messages.at(-1)?.model} · ` : ""}
          <button type="button" className="chat-toggle-btn" onClick={chat.clear} disabled={loading}>
            Limpar conversa
          </button>
        </p>
      )}
    </div>
  );
}
