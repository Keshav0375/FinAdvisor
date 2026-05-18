import { useCallback, useState } from "react";
import { streamChat, type SSEEvent } from "@/lib/api";
import { useUser } from "@/hooks/useUser";

export interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  toolUseId: string;
  status: "running" | "done";
  startedAt: number;
  durationMs?: number;
}

export interface Citation {
  index: number;
  sourceTitle: string;
  regulatoryRef: string | null;
  lastReviewedAt: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  citations: Citation[];
  isStreaming: boolean;
  error?: string;
}

let messageCounter = 0;
function nextId(): string {
  return `msg-${++messageCounter}-${Date.now()}`;
}

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const { currentUser } = useUser();

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || isStreaming) return;

      const userMessage: Message = {
        id: nextId(),
        role: "user",
        content,
        toolCalls: [],
        citations: [],
        isStreaming: false,
      };

      const assistantId = nextId();
      const assistantMessage: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
        toolCalls: [],
        citations: [],
        isStreaming: true,
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      setIsStreaming(true);

      try {
        await streamChat(content, currentUser.sub, undefined, (event: SSEEvent) => {
          if (event.event === "message") {
            const data = JSON.parse(event.data) as { type: string; content: string };
            if (data.type === "text") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: m.content + data.content } : m
                )
              );
            }
          } else if (event.event === "tool") {
            const data = JSON.parse(event.data) as {
              tool: string;
              input: Record<string, unknown>;
              tool_use_id: string;
            };
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      toolCalls: [
                        ...m.toolCalls,
                        {
                          tool: data.tool,
                          input: data.input,
                          toolUseId: data.tool_use_id,
                          status: "running" as const,
                          startedAt: Date.now(),
                        },
                      ],
                    }
                  : m
              )
            );
          } else if (event.event === "tool_result") {
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== assistantId) return m;
                const updated = [...m.toolCalls];
                const lastIdx = updated.length - 1;
                if (lastIdx >= 0 && updated[lastIdx].status === "running") {
                  updated[lastIdx] = {
                    ...updated[lastIdx],
                    status: "done",
                    durationMs: Date.now() - updated[lastIdx].startedAt,
                  };
                }
                return { ...m, toolCalls: updated };
              })
            );
          } else if (event.event === "citation") {
            const data = JSON.parse(event.data) as {
              index: number;
              source_title: string;
              regulatory_ref: string | null;
              last_reviewed_at: string;
            };
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      citations: [
                        ...m.citations,
                        {
                          index: data.index,
                          sourceTitle: data.source_title,
                          regulatoryRef: data.regulatory_ref,
                          lastReviewedAt: data.last_reviewed_at,
                        },
                      ],
                    }
                  : m
              )
            );
          } else if (event.event === "error") {
            const data = JSON.parse(event.data) as { message: string };
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, error: data.message, isStreaming: false } : m
              )
            );
          } else if (event.event === "done") {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, isStreaming: false } : m))
            );
          }
        });
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  error: err instanceof Error ? err.message : "Connection failed",
                  isStreaming: false,
                }
              : m
          )
        );
      } finally {
        setIsStreaming(false);
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, isStreaming: false } : m))
        );
      }
    },
    [currentUser.sub, isStreaming]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return { messages, sendMessage, isStreaming, clearMessages };
}
