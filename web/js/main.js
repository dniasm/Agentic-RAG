/** The Document Agent page: finds its elements, makes the shared helpers, and wires the feature modules together.
    Everything site-specific (ids, endpoints, keys) is here or in js/app/; js/lib/ is reusable on any site. */
import { createApi } from "./lib/api.js";
import { elements } from "./lib/dom.js";
import { makeResizable } from "./lib/resizer.js";
import { responsivePlaceholder } from "./lib/textarea.js";
import { createToast } from "./lib/toast.js";
import { createChat } from "./app/chat.js";
import { createLibrary } from "./app/library.js";
import { createMeter } from "./app/meter.js";
import { createQueue } from "./app/queue.js";

const api = createApi();
const toast = createToast(document.getElementById("toast"));

const library = createLibrary({
  api, toast,
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
