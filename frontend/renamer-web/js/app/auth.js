/** The sign-in screen: log in, or create an account (with an invite code when the server asks for one). */
import { postJson } from "../lib/api.js";

/** els: screen, form, intro, user, pass, inviteRow, invite, error, submit, switchMode. onSignedIn(user) runs once. */
export function createAuth({ els, api, onSignedIn }) {
  let mode = "login";      /* or "signup" */
  let signups = "closed";  /* "first", "invite" or "closed", from the server */

  function render() {
    const signup = mode === "signup";
    els.intro.textContent = signup
      ? (signups === "first" ? "Create the first account. It becomes the owner of the existing library." : "Create an account.")
      : "Log in to your library.";
    els.submit.textContent = signup ? "Create account" : "Log in";
    els.pass.autocomplete = signup ? "new-password" : "current-password";
    els.inviteRow.hidden = !(signup && signups === "invite");
    els.switchMode.hidden = signups === "closed" && !signup;
    els.switchMode.textContent = signup ? "I already have an account" : "Create an account";
    els.error.textContent = "";
  }

  els.switchMode.addEventListener("click", () => { mode = mode === "login" ? "signup" : "login"; render(); });
  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    els.submit.disabled = true;
    try {
      const body = { username: els.user.value.trim(), password: els.pass.value, invite: els.invite.value.trim() };
      const { user } = await api(mode === "signup" ? "/api/auth/signup" : "/api/auth/login", postJson(body));
      els.screen.hidden = true;
      onSignedIn(user);
    } catch (err) {
      els.error.textContent = err.message;
    } finally {
      els.submit.disabled = false;
    }
  });

  return {
    /** Show the screen; with no account yet, open on sign-up. */
    show(state) {
      signups = state.signups;
      mode = signups === "first" ? "signup" : "login";
      render();
      els.screen.hidden = false;
      els.user.focus();
    },
  };
}
