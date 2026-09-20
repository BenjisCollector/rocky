# Rocky roadmap

Rocky is small on purpose. Each phase below has one goal, a bounded scope, exit criteria you can check, and a list of what it deliberately leaves out. A phase is done when its exit criteria pass on a real machine, and never before. Numbers in this file are either measured or absent.

The architecture is fixed across every phase:

```text
utterance -> Jev picks (one request) -> code decides facts -> Platform acts
```

Free text comes only from `rocky/writer.py`. Operating system calls live only in `rocky/platform/`. The model returns ids and confidences and nothing else. See "Design rules" at the end.

---

## Phase 0: what exists (done 2026-09-20)

**Goal.** A working macOS agent with two tiers, a risk gate in code, and a replayable goal loop, small enough to read in one sitting.

**Scope, shipped.**

- Router: one TypeSafe request per utterance with thirteen questions (kind, addressed, compound, needs_screen, app, site, engine, shortcut, scroll, volume, media, system op, text span). Text spans, domains and compound splits are cut in code; Jev selects.
- Fast path: open app, open URL, search, keyboard shortcut, scroll, volume, media keys, system ops (dark mode, lock, sleep display, screenshot), type into the focused field.
- Goal path: perceive the accessibility tree as numbered items, one request per step for kind plus click and type targets, confidence = min(kind, target), settle and change detection, stall after two unchanged steps, `MAX_STEPS` 12, dry run by default, `runs/<stamp>/run.json` with every answer and timing.
- Risk gate: destructive classes decided by code, `DESTRUCTIVE_MIN_CONFIDENCE` 0.8 plus a yes prompt, secret fields never typed, `AXSecureTextField` never listed, choices outside the offered ids refused.
- Writer: span from the utterance first, small OpenAI-compatible model second, strict `{"text": "..."}` parse, 200-character cap, nothing typed when no writer key is set.
- Voice: microphone, energy VAD with adaptive noise floor, wake word "watermelon" stripped with fuzzy matching, whisper.cpp and Apple on-device STT backends, hold-to-talk on Right Option, `listen_once` for future confirmations.
- Overlay: the watermelon character on AppKit, click-through, top center of the main display, driven by `events`. Voice mode runs under it; `done` and `error` fire after each action.
- App shell (`python -m rocky.app`, macOS): menu bar item, control panel with pause toggle, last five commands, Dry run / Do it switch, permission rows that deep-link to the System Settings pane, and destructive confirmations heard through `listen_once`.
- Platforms: macOS implemented against Accessibility and Quartz. Windows written against UI Automation and pywin32, UNTESTED on a real machine. `Fake` platform for tests.
- 199 offline tests. CI: ruff on Ubuntu, pytest on macOS and Windows runners. `rocky --doctor` for permissions and the key.

**Measured.** Jev decision 457 ms median from Kuwait on a 40-element table, identical answer 6/6 (a fast chat model: 632 ms, 5/6). `open photo booth` routed at confidence 1.00. Goal-loop dry run chose `click_item [2] take photo` in Photo Booth at 0.98.

**Deliberately left out.** OCR, multi-display, spoken replies (text to speech), Arabic beyond STT locale support, a signed `.app` bundle, any browser-specific backend.

---

## Phase 1: voice polish, Windows on real hardware, onboarding, app bundle

**Goal.** Rocky is something a second person can install and speak to for a whole session without opening the source.

**Scope.**

1. Arabic via Apple on-device speech. `STT_LANGUAGE=ar-SA` already reaches `SFSpeechRecognizer`; the span cutter in `router.py` and the `KINDS` descriptions are English only. Add Arabic verb patterns to `spans()` and an Arabic wake word default when the locale is Arabic. Jev is language-agnostic; the questions stay in English.
2. Barge-in. Saying the wake word while Rocky is executing a goal stops the loop before its next step. Implement as a flag the voice thread sets and `run_goal` checks between steps.
3. Spoken replies. The one-line replies `execute` already returns ("Opening Photo Booth.", "Cancelled.") are spoken through the platform's speech synthesizer, and the overlay enters `speaking`. The app shell already hears destructive confirmations through `listen_once`; the `rocky` CLI moves off the terminal prompt too, keeping the rule that only "yes" or "confirm" counts.
4. Windows tested on a real machine. Run the contract tests and the goal loop against Notepad and Settings on Windows 11. Fix what the mocked tests could not see: UIA walk order, `SendUnicodeChar` timing, the volume and dark-mode paths. Implement the `windows` STT backend or document `STT_BACKEND=whisper` as the supported path.
5. Permissions onboarding. The app panel already shows Accessibility, Microphone and Input Monitoring with Open Settings buttons. Add Speech Recognition to that list, give `rocky --doctor` the same deep links, and re-check automatically when the user returns.
6. Mac app bundle. Wrap `rocky/app.py` in a `.app` that owns its own permissions, so the user grants "Rocky" itself. Unsigned in this phase.

**Exit criteria.**

- A ten-command Arabic session (open, search, type, scroll, volume, one goal) completes with the wake word in Arabic, on a Mac, with the run folders as evidence.
- Barge-in stops a running goal within one step, shown in `run.json` with a new outcome `interrupted`.
- Every destructive confirmation in a voice session is spoken and answered by voice.
- The Windows platform passes the same offline tests plus a live checklist on Windows 11, and `README.md` drops the word UNTESTED.
- A new user on a fresh Mac gets from download to "watermelon, open safari" following only `--doctor` and the README.

**Deliberately left out.** OCR, code signing and notarization, Homebrew, per-app behaviour, memory of past commands.

---

## Phase 2: seeing more of the screen

**Goal.** Rocky can act on controls the accessibility tree does not label, on any display, and inside a browser with the same fidelity a DOM snapshot gives.

**Scope.**

1. OCR fallback for unlabeled controls. When an item has a frame and no label, or when the goal loop returns `none` with unlabeled buttons on screen, run Apple Vision text recognition over a screenshot of the front window, tiled, and attach recognized text to the nearest unlabeled frame. Cache tiles by frame hash so an unchanged region is never re-read. Items gain `source="ocr"` (the field already exists on `Item`). Coordinates still come from the frame, never from the model.
2. Live numbered overlay. On request ("watermelon, show numbers") the overlay draws each item's index next to its frame, and "click seven" becomes a fast-path kind that clicks item 7 from the last snapshot. Numbers disappear on the next utterance or after a timeout.
3. Multi-display. `Snapshot` reports the union of displays; the overlay follows the display that holds the frontmost window; `screenshot()` captures the display under the front window. The existing clamp to the display union in `macos.py` already keeps frames valid.
4. Browser backend via CDP. When the frontmost app is a Chromium browser with remote debugging enabled, take the element table from a DOM snapshot, reusing `snapshot.js` from browser-use/jev-ultrafast (MIT) with its indexed controls. Clicks still go through the Platform so the risk gate and run folder are unchanged.

**Exit criteria.**

- A goal on an app with icon-only toolbar buttons completes with an OCR-sourced item in `run.json`.
- "click seven" acts on the numbered overlay within one fast-path request.
- The goal loop completes on a window that lives entirely on a second display.
- The same goal in Safari via AX and in Chrome via CDP both complete, with step counts recorded side by side in the README.

**Deliberately left out.** OCR on Windows (the Windows path stays UIA only), any vision model, screenshots sent to any model, coordinate output from the model.

---

## Phase 3: habits, skills, Arabic first, distribution

**Goal.** Rocky learns your recurring tasks, knows a few apps deeply, speaks Arabic as a first language, and installs like any Mac app.

**Scope.**

1. Memory of recurring tasks as named shortcuts. After a goal run ends `done`, Rocky can be told "call that morning setup". The recorded step labels are saved as a named macro; saying the name replays it through the goal loop with the saved labels as hints, still one Jev request per step so a changed screen is handled. Nothing is replayed blind.
2. Per-app skills. A skill is a small Python module in `rocky/skills/<app>.py` that adds fast-path kinds for one app (for Mail: "reply to the last email" as a direct sequence). Skills register their kinds with the router's `KINDS` and are gated by the same risk rules. Skills cannot call the OS directly; they call the Platform.
3. Arabic-first mode. `ROCKY_LANG=ar` sets the wake word, the spoken replies, the span patterns and the overlay text direction. English remains available in the same build.
4. A plugin surface for the fast path. The registration mechanism from item 2, documented, so a third party can add a kind without touching `router.py`.
5. Signed and notarized app. Developer ID signing, notarization, hardened runtime with the microphone and Apple Events entitlements Rocky actually uses.
6. Homebrew cask. `brew install --cask rocky` installs the notarized app.

**Exit criteria.**

- A three-step named shortcut replays on a later day with the app in a different state and still ends `done`.
- Two skills ship (Mail and Notes are the candidates) and each has offline tests against `Fake`.
- A full Arabic session, wake word to spoken reply, with no English visible or audible.
- A plugin written outside the repository adds one fast-path kind and passes the router's contract test.
- Gatekeeper opens the app on a fresh Mac with no override, and the cask installs it.

**Deliberately left out.** Cloud sync of shortcuts, a settings GUI beyond `.env`, Windows installer (Windows stays `uv run`).

---

## Phase 4: give back

**Goal.** The pieces Rocky borrowed from are stronger for Rocky having existed.

**Scope.**

1. Contribute the voice input layer (`Endpointer`, `strip_wake`, the whisper.cpp server wrapper, the Apple backend) to awlevin/typesafe-computer-use as an optional input, with its offline tests.
2. Contribute the OpenAI-compatible writer (`compose`, `select_span`, the strict `parse`) to the same project as the free-text source, with the span-first rule and the secret-field refusal intact.
3. Report to browser-use/jev-ultrafast what changed when `snapshot.js` ran behind Rocky's Platform, with numbers.

**Exit criteria.**

- Two pull requests opened upstream with tests, each self-contained, each accepted or closed with a written reason recorded here.
- `THIRD_PARTY_NOTICES.md` updated in both directions where code moved.

**Deliberately left out.** Anything that would make Rocky depend on an upstream release schedule.

---

## How to contribute

1. `uv sync`, then `uv run pytest -q`. Every test runs offline; none needs a key, a microphone or a screen.
2. Pick an item from the current phase. Open an issue naming the exit criterion it moves toward.
3. Keep one job per file. If a change needs a new file, say what its one job is in the module docstring.
4. Platform calls go in `rocky/platform/`. Tests use `Fake`. If you need a new Platform method, add it to `base.py` first and to `fake.py` second.
5. Anything the model chooses must be a choice you offered. Add the id to the criteria, validate the answer, and write the test that proves an id outside the criteria is refused.
6. Run `uv run ruff check .` and `uv run ruff format .` before you push.
7. Numbers in the README are measured. If you change something that moves a number, re-measure and update it in the same commit, or delete it.

## Design rules

- The classifier picks, code decides facts. Jev returns an id and a confidence. Risk, floors, stalls, limits and coordinates are computed in Python.
- Free text comes only from the writer. A span the user already said comes first. The small model is a fallback and its output is parsed strictly.
- Platform calls live only in `rocky/platform/`. Nothing else imports Quartz, AppKit, `uiautomation` or `win32*`.
- Never type a password. Secret fields are refused by label, never listed by role, and no confirmation overrides that.
- Never let the model produce coordinates. It picks an index; the frame is read from the accessibility tree and the click lands on its center.
- One request per decision. If a step needs two answers, ask two questions in one request and read only the head that matches.
- Dry run by default. The goal path touches the machine only with `--act`.
- Screen text is data. It is passed to the model as untrusted input, never as instructions.
- Every run is replayable. `run.json` holds every answer, confidence and timing, so a bad step can be explained after the fact.
