import { useState } from "react";
import type { ChatMessage, PersonaSummary } from "../api/client";
import { sendChatMessage } from "../api/client";

interface Props {
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

export function ChatAssistant({ personas, personaId, onUsePrompt }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const personaName = personas.find((p) => p.id === personaId)?.name;

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const next = [...messages, { role: "user", content: text } as ChatMessage];
    setMessages(next);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const reply = await sendChatMessage(personaId || undefined, next);
      setMessages([...next, reply]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
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
        {loading && <p className="muted small">Assistente digitando...</p>}
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
          Enviar
        </button>
      </div>
    </div>
  );
}
