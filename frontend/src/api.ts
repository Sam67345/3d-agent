// M13 API 客户端：只做 fetch 封装，不含任何业务逻辑。
// 后端是 api_server.py（FastAPI，端口 7861），它内部复用 web_app 的 Agent。
export const API = "http://127.0.0.1:7861";

export type Step = {
  skill: string;
  args: Record<string, unknown>;
  reason?: string;
};

export type ChatReply = {
  status: "confirm" | "done" | "error" | "clarify" | "cancelled";
  reply: string;
  steps?: Step[];
  plan_card?: string;
  panel?: string;
  dropped?: unknown[];
};

export type SessionItem = {
  title: string;
  saved_at: string;
  n_slices: number;
  n_virtual: number;
  path: string;
  label: string;
};

export type FigureItem = { name: string; path: string; abs: string; mtime: number };

export type PanelData = {
  markdown: string;
  slices: { sample_id: string; n_spots: number; n_genes: number }[];
  n_virtual: number;
  run_dir: string;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* 忽略非 JSON 错误体 */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => req<{ status: string; agent: boolean; error: string | null }>("/api/health"),

  chat: (message: string, skipConfirm: boolean) =>
    req<ChatReply>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, skip_confirm: skipConfirm }),
    }),

  confirm: (steps: Step[]) =>
    req<ChatReply>("/api/confirm", {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),

  cancel: () => req<ChatReply>("/api/cancel", { method: "POST" }),

  panel: () => req<PanelData>("/api/panel"),

  sessions: () =>
    req<{ sessions: SessionItem[]; latest_exists: boolean; latest_saved_at: string }>(
      "/api/sessions",
    ),

  newSession: () =>
    req<{ status: string; archived: string | null; sessions: SessionItem[] }>(
      "/api/sessions/new",
      { method: "POST" },
    ),

  restoreSession: (path: string) =>
    req<{ status: string; reply: string; panel: string }>("/api/sessions/restore", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  figures: () => req<{ figures: FigureItem[]; label: string }>("/api/figures"),

  addFolder: (folder: string) =>
    req<{ status: string; added: number; figures: FigureItem[] }>("/api/figures/folder", {
      method: "POST",
      body: JSON.stringify({ folder }),
    }),

  /** 上传前端弹窗选中的本地 html（浏览器拿不到真实路径，只能传内容） */
  uploadFigures: (files: { name: string; content_b64: string }[]) =>
    req<{
      status: string;
      saved: string[];
      skipped: string[];
      figures: FigureItem[];
      label: string;
    }>("/api/figures/upload", {
      method: "POST",
      body: JSON.stringify(files),
    }),

  llmConfig: () =>
    req<{ base_url: string; model: string; api_key_masked: string; has_key: boolean }>(
      "/api/llm-config",
    ),

  saveLlmConfig: (cfg: { api_key: string; base_url: string; model: string }) =>
    req<{ status: string; message: string; header: string }>("/api/llm-config", {
      method: "POST",
      body: JSON.stringify(cfg),
    }),
};

/** 3D 图iframe 的 src（走后端，带路径校验） */
export function figureSrc(path: string) {
  return `${API}/api/figures/file?path=${encodeURIComponent(path)}`;
}