/** The Document Agent page: signs the user in, then finds the app's elements, makes the shared helpers and wires the
    feature modules together. Everything site-specific (ids, endpoints, keys) is here or in js/app/; js/lib/ is
    reusable on any site. */
import { createApi, postJson } from "./lib/api.js";
import { elements } from "./lib/dom.js";
import { makeResizable } from "./lib/resizer.js";
import { responsivePlaceholder } from "./lib/textarea.js";
import { createToast } from "./lib/toast.js";
import { createAuth } from "./app/auth.js";
import { createChat } from "./app/chat.js";
import { createLibrary } from "./app/library.js";
import { createMeter } from "./app/meter.js";
import { createPreview } from "./app/preview.js";
import { createQueue } from "./app/queue.js";

let started = false;
const api = createApi("", { onUnauthorized: () => { if (started) location.reload(); } });   /* login expired */
const toast = createToast(document.getElementById("toast"));

function startApp(user) {
  started = true;
  document.getElementById("app").hidden = false;
  document.getElementById("user-name").textContent = user.username;
  document.getElementById("log-out").addEventListener("click", async () => {
    try { await api("/api/auth/logout", postJson({})); } finally { location.reload(); }
  });

  const preview = createPreview({
    api, toast, els: elements({ dialog: "preview", title: "preview-title", body: "preview-body", close: "preview-close" }),
  });

  const library = createLibrary({
    api, toast, onPreview: preview.show,
    els: elements({ panel: "library", list: "doc-list", count: "doc-count", empty: "empty-library",
                    fileInput: "file-input", toggle: "toggle-library" }),
  });

  const queue = createQueue({
    api, toast,
    els: elements({ panel: "queue", list: "queue-list", count: "queue-count" }),
    onTaskFinished: () => library.load(),
  });

  const chatEls = elements({ messages: "messages", welcome: "welcome", composer: "composer", input: "input",
                             send: "send", newChat: "new-chat" });
  const chat = createChat({
    api,
    els: { ...chatEls, suggestions: chatEls.welcome },
    sessionKey: `renamer-session-${user.id}`,
    onPreview: (id) => { const doc = library.get(id); if (doc) preview.show(doc); else toast("That document is gone."); },
    onUndo: async (id) => {
      try {
        const result = await api(`/api/documents/${id}/undo`, postJson({}));
        library.set(result.documents, [id]);
        toast(`Undid the edit to “${result.title ?? "the document"}”`);
      } catch (e) {
        toast(`Couldn't undo: ${e.message}`);
        throw e;
      }
    },
    onReply: (result) => {
      library.set(result.documents, result.actions.map((a) => a.doc_id));
      if (result.actions.some((a) => a.type === "queue")) queue.refresh();
    },
  });

  const meter = createMeter({
    api,
    els: elements({ meter: "meter", text: "meter-text", timer: "meter-timer", fill: "meter-fill" }),
  });

  makeResizable({
    handle: document.getElementById("resizer"), panel: document.getElementById("library"),
    cssVar: "--library-width", min: 240, max: 600, initial: 320, storageKey: "renamer-library-width",
  });
  responsivePlaceholder(chatEls.input, "(max-width: 640px)", "Rename, edit or undo a document…");

  library.load();
  chat.loadSession();
  queue.refresh();
  meter.start();
}

const auth = createAuth({
  api,
  els: elements({ screen: "auth", form: "auth-form", intro: "auth-intro", user: "auth-user", pass: "auth-pass",
                  inviteRow: "auth-invite-row", invite: "auth-invite", error: "auth-error",
                  submit: "auth-submit", switchMode: "auth-switch" }),
  onSignedIn: startApp,
});

const state = await api("/api/auth/state");
if (state.user) startApp(state.user);
else auth.show(state);
