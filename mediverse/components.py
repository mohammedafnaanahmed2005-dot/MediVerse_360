"""Inline CCv2 component capturing keystroke timing for the fixed consent phrase."""

from __future__ import annotations

import streamlit as st

_HTML = """
<div class="tc">
  <label class="lbl" for="tc-in"></label>
  <input id="tc-in" type="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" />
  <div class="row">
    <button id="tc-reset" type="button">Clear</button>
    <button id="tc-done" type="button" class="primary">Submit sample</button>
  </div>
  <div id="tc-msg" class="msg"></div>
</div>
"""

_CSS = """
.tc { font-family: var(--st-font); color: var(--st-text-color); }
.lbl { display:block; margin-bottom: .4rem; font-size: .9rem; }
#tc-in { width: 100%; box-sizing: border-box; padding: .55rem .7rem; font-size: 1rem;
         border: 1px solid var(--st-border-color, #ccc); border-radius: var(--st-base-radius, .5rem);
         background: var(--st-secondary-background-color); color: var(--st-text-color); }
.row { display:flex; gap:.5rem; margin-top:.5rem; }
button { padding:.4rem .9rem; border-radius: var(--st-base-radius, .5rem); cursor:pointer;
         border:1px solid var(--st-border-color, #ccc); background: var(--st-background-color); color: var(--st-text-color); }
button.primary { background: var(--st-primary-color); color: #fff; border-color: var(--st-primary-color); }
.msg { margin-top:.4rem; font-size:.85rem; opacity:.8; }
"""

_JS = """
const STATE = new WeakMap()
export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const input = parentElement.querySelector("#tc-in")
  const msg = parentElement.querySelector("#tc-msg")
  parentElement.querySelector(".lbl").textContent = 'Type exactly: "' + (data?.phrase ?? "") + '"'
  let st = STATE.get(parentElement)
  if (!st) { st = { events: [] }; STATE.set(parentElement, st) }
  const reset = () => { st.events = []; input.value = ""; msg.textContent = ""; input.focus() }
  input.onpaste = (e) => { e.preventDefault(); msg.textContent = "Pasting is disabled; please type the phrase." }
  input.onkeydown = (e) => {
    if (e.key === "Backspace") { reset(); msg.textContent = "Restarted: corrections are not allowed in a sample."; return }
    if (e.key.length === 1) st.events.push({ k: e.code + ":" + st.events.length, t: "d", ms: performance.now() })
  }
  input.onkeyup = (e) => {
    if (e.key.length !== 1) return
    for (let i = st.events.length - 1; i >= 0; i--) {
      const ev = st.events[i]
      if (ev.t === "d" && ev.k.startsWith(e.code + ":") && !ev.closed) {
        ev.closed = true
        st.events.push({ k: ev.k, t: "u", ms: performance.now() })
        break
      }
    }
  }
  parentElement.querySelector("#tc-reset").onclick = reset
  parentElement.querySelector("#tc-done").onclick = () => {
    const t0 = st.events.length ? st.events[0].ms : 0
    const events = st.events.map(e => ({ k: e.k, t: e.t, ms: Math.round((e.ms - t0) * 10) / 10 }))
    setTriggerValue("sample", { phrase: input.value, events })
    reset()
    msg.textContent = "Sample captured."
  }
  return () => { STATE.delete(parentElement) }
}
"""

_V2 = getattr(getattr(st, "components", None), "v2", None)
_REGISTERED: dict[int, object] = {}


def _component():
    """Register once per Streamlit runtime (registering at import time, before a runtime
    exists, leaves a broken registry entry on Streamlit 1.52)."""
    if _V2 is None:
        return None
    from streamlit.runtime import Runtime
    rid = id(Runtime.instance()) if Runtime.exists() else 0
    if rid not in _REGISTERED:
        _REGISTERED[rid] = _V2.component("mediverse_typing_capture", html=_HTML, css=_CSS, js=_JS)
    return _REGISTERED[rid]


def typing_capture(phrase: str, *, key: str):
    """Returns the submitted sample dict on the rerun after the user presses Submit, else None.

    If this Streamlit build lacks Custom Components v2, keystroke timing cannot be captured;
    the caller then gets None and the auth flow treats typing as UNAVAILABLE (OTP fallback).
    """
    comp = _component()
    if comp is None:
        st.caption(":material/keyboard_off: Typing-rhythm capture is unavailable in this runtime; "
                   "a one-time code will be required.")
        return None
    result = comp(data={"phrase": phrase}, key=key, on_sample_change=lambda: None)
    return getattr(result, "sample", None)
