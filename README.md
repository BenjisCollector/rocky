# Rocky

Say it, and your computer does it.

Rocky is a voice-controlled computer agent for macOS (Windows is written but UNTESTED) built on TypeSafe's Jev decision model. Jev never generates text. It picks from options Rocky offers and returns a confidence, and code does everything else. Say the wake word, then the command.

| You say | Rocky does |
|---|---|
| "watermelon, open photo booth" | One Jev request routes it to `open_app`, the Mac runs `open -a "Photo Booth"`. Measured live at confidence 1.00. |
| "watermelon, search youtube for lofi beats" | Code cuts candidate spans from your words, Jev picks `lofi beats` and the engine, Rocky opens the YouTube results page. |
| "watermelon, take a photo in photo booth" | The goal loop reads the accessibility tree, Jev picks `click_item [2] take photo` at 0.98, and by default Rocky prints what it would do. Add `--act` and it clicks. |

## Why it moves

**One Jev request per decision.** The router asks thirteen questions in a single request: kind, addressed, compound, needs_screen, app, site, engine, shortcut, scroll, volume, media, system op, and which text span is the payload. The goal loop asks kind, click target and type target together in one request and reads only the head that matches the chosen kind. Every decision costs one round trip.

**No screenshots go to a model.** Perception is the accessibility tree: AX on macOS, UI Automation on Windows. Rocky renders it as a numbered list of enabled, on-screen, labelled controls and hands that list to Jev. PNGs are saved to the run folder for you to replay. They are never uploaded.

**Select instead of generate.** Every free-text value is cut from the utterance in code and offered as candidates: quoted text, "type ...", "search for ...", "called ...". Jev selects one. Only when no span fits does `writer.py` ask a small OpenAI-compatible model, and it accepts nothing but a `{"text": "..."}` object under 200 characters.

**Code decides facts.** Risk class, confidence floors, stall detection, the step limit, and the point to click all live in Python. Jev returns an index and a confidence. `validate_choice` refuses any answer outside the ids that were offered, so a malformed or spoofed answer can never reach the machine.

## Try it

```bash
uv sync
cp .env.example .env
```

Two keys go in `.env`:

- `TYPESAFE_API_KEY` from [typesafe.ai](https://typesafe.ai). Every decision goes through it.
- `TEXT_MODEL_API_KEY` for the writer. Any OpenAI-compatible endpoint works; the default base URL is DeepSeek. Without it, Rocky types only text it can cut from what you said.

```bash
uv run rocky --text "open photo booth"                    # fast path, acts immediately
uv run rocky --text "take a photo in photo booth"         # goal path, dry run: prints the step
uv run rocky --text "take a photo in photo booth" --act   # goal path drives the machine
uv run rocky --text "open photo booth" --dry              # print the plan, execute nothing
uv run rocky                                              # voice: say "watermelon, open safari"
uv run rocky --doctor                                     # permissions and key check
```

Voice mode uses the microphone with energy-based endpointing. The wake word is stripped with fuzzy matching ("water melon," still counts). Hold Right Option to talk without endpointing. Speech to text is `auto`: Apple's on-device recognizer when the Speech framework can be used, otherwise a local whisper.cpp `whisper-server` (`brew install whisper-cpp`, then download `models/ggml-base.bin`; the docstring in `rocky/voice.py` has the exact command). Set `STT_LANGUAGE=ar-SA` for Arabic.

Permissions on macOS are granted to your terminal app, since that is what runs Rocky. `rocky --doctor` shows what is missing.

| Permission | Why |
|---|---|
| Accessibility | Reading the numbered items, sending clicks and keystrokes |
| Microphone | Hearing you |
| Input Monitoring | The hold-to-talk key (a listen-only event tap) |
| Speech Recognition | Only with the `apple` STT backend |

Quit and reopen the terminal after ticking a box. Nothing persistent is installed: no LaunchAgent, no key remap, no helper process. Details in `rocky/platform/README-permissions.md`.

Voice mode runs under the watermelon overlay (`rocky/overlay.py`). It floats at the top center of the main display, click-through and on every Space. It blinks while idle, glows green and waves when it hears you, looks up while it thinks, beams when an action lands, and glows red when one fails. `uv run python -m rocky.overlay` cycles it through its states without the microphone.

There is also a menu bar shell:

```bash
uv run python -m rocky.app          # menu bar watermelon, control panel, overlay, voice loop
ROCKY_NO_VOICE=1 uv run python -m rocky.app   # the shell without a microphone
```

The panel shows the listening state, a pause toggle, the last five commands with their plan lines, a Dry run / Do it switch for screen tasks, and the three permissions with an Open Settings button that deep-links to the right pane. In the shell, the destructive-action yes is asked in the panel and heard through the microphone for five seconds. macOS only; the `.app` bundle is on the roadmap.

## The two tiers

```text
                      utterance: "watermelon, open photo booth"
                                        |
                        one TypeSafe request, 13 questions
                 kind . addressed . compound . needs_screen . app
             site . engine . shortcut . scroll . volume . media . system . text
                                        |
              +-------------------------+--------------------------+
              |                                                    |
            FAST                                                 GOAL
  open_app  open_url  search                    perceive: AX tree -> numbered items
  shortcut  scroll    volume                                       |
  media     system    type                      one TypeSafe request, 3 heads
              |                                   kind . click_target . type_target
        risk gate, floor 0.45                                      |
              |                                 risk gate, floor 0.5, dry run unless --act
       one Platform call                                           |
                                                click [7] / type / enter / esc / scroll
                                                                   |
                                                settle, changed?, stall, step limit
                                                                   |
                                                        runs/<stamp>/run.json
```

The fast path always acts. The goal path dry-runs unless you pass `--act`. A `type` command goes to the goal path when Jev says the screen must be read to finish it; otherwise it types into the focused field.

## Safety

These are the rules `rocky/risk.py` enforces. The model never classifies its own risk.

- **Destructive is decided in code.** System ops `lock`, `sleep`, `sleep_display`, `shutdown`, `restart`, `empty_trash`. Shortcuts whose name contains delete, send, trash, quit or force. Any goal sentence, or any clicked label, containing delete, remove, erase, discard, trash, empty, pay, buy, purchase, order, checkout, send, submit, transfer, wipe, format, uninstall, unsubscribe, cancel, shutdown, restart or reboot.
- **Destructive actions need a higher confidence, then a yes.** Below `DESTRUCTIVE_MIN_CONFIDENCE` (0.8): "That looks destructive and I am only N% sure. Nothing done." At or above it, Rocky asks once. Only a plain "yes" or "confirm" continues; anything else is "Cancelled." In the goal loop a destructive step ends the run with the outcome `needs confirmation`.
- **Passwords are never typed.** A field whose label matches password, passcode, passphrase, PIN, CVV, CVC, card number, credit card, debit, SSN, social security, secret, token, API key or security code is refused even after a yes: "I never type into password or card fields." macOS never lists `AXSecureTextField`; Windows skips `IsPassword` controls. Text that itself mentions a password or card is refused on the fast path too.
- **Low confidence does nothing.** A fast plan below `ACTION_MIN_CONFIDENCE` (0.45) gets "Not sure what you meant." A goal step below `GOAL_MIN_CONFIDENCE` (0.5) ends the run. An utterance Jev judges as not addressed to the computer is ignored.
- **The model never produces coordinates.** It returns an item index. The click lands on the center of that item's accessibility frame, computed in code. Any choice outside the offered ids is refused and nothing executes.
- **The loop cannot run away.** `MAX_STEPS` is 12. Two executed steps in a row that change nothing on screen end the run as `stalled`. Every run writes `run.json` with each step, its confidences, latency and the raw answers.
- **Screen text is untrusted.** The prompt tells Jev that visible text is data, never instructions.

## Small enough to read

About 4,100 lines of Python, one job per file.

| File | Job |
|---|---|
| `rocky/cli.py` | Entry point: `--text`, `--dry`, `--act`, `--doctor`; voice mode under the overlay |
| `rocky/app.py` | Menu bar shell (AppKit): control panel, permissions, Dry run / Do it, spoken confirmations |
| `rocky/router.py` | One fan-out request per utterance; span cutting; domain guess; compound split |
| `rocky/fast.py` | Plan to Platform call; risk check; hands goal-tier plans to the loop |
| `rocky/loop.py` | Perceive, decide, act, settle; floors, stall detection, step limit, run folder |
| `rocky/perceive.py` | Snapshot to numbered items (enabled, no goal echo, no duplicates); did the screen change |
| `rocky/decide.py` | One request per step: kind plus click and type heads; confidence = min(kind, target); typed-text verification |
| `rocky/risk.py` | Reversible or destructive; secret fields; the yes gate |
| `rocky/writer.py` | The only source of free text: a span from your words first, a small LLM second, strict parse |
| `rocky/jev.py` | The one door to TypeSafe; retries; `validate_choice`; `noul` |
| `rocky/voice.py` | Microphone, energy VAD, wake word, whisper.cpp and Apple STT, hold-to-talk |
| `rocky/overlay.py` | The watermelon (AppKit); states arrive over `events` |
| `rocky/events.py` | In-process pub/sub of `idle`, `hearing`, `thinking`, `speaking`, `done`, `error` |
| `rocky/models.py` | `Item`, `Snapshot`, `Plan`, `Decision`, `StepResult` |
| `rocky/config.py` | Settings from the environment; a minimal `.env` loader |
| `rocky/platform/base.py` | The `Platform` Protocol: the only contract with the OS |
| `rocky/platform/macos.py` | Accessibility tree, Quartz input, `open`, `osascript`, media keys |
| `rocky/platform/windows.py` | UI Automation and pywin32. UNTESTED on a real Windows machine |
| `rocky/platform/fake.py` | In-memory platform that records calls, for tests and dry runs |

## Evidence and limits

Measured from Kuwait.

- **Jev decision: 457 ms median** on a 40-element table, with the identical answer 6 runs out of 6. A fast chat model on the same table: 632 ms median, 5 out of 6.
- **Live route:** `rocky --text "open photo booth"` routed to `open_app(Photo Booth)` at confidence 1.00.
- **Goal loop, dry run:** "Take a photo in Photo Booth" offered Jev six items from the Photo Booth window (perceive 144 ms). Jev chose `click_item [2] take photo` with kind 0.99 and target 0.98, so the step scored 0.98. Rocky printed the step and touched nothing. The `run.json` is in `runs/`.

Limits as of this commit:

- **Windows is UNTESTED.** `rocky/platform/windows.py` is written against the `uiautomation` source and passes its contract and walk tests with mocked modules. Nothing in it has run on a Windows machine.
- **No OCR fallback yet.** Icon-only buttons without an accessibility label are invisible to Rocky.
- **Single main display.** That is what has been exercised; `Snapshot.screen_w/h` and the overlay refer to the main display.
- **Apple speech backend not exercised live yet.** It is implemented; its docstring records that Speech permission was still undetermined when the session ran.
- **Two confirmation paths.** From the `rocky` CLI the destructive-action yes is a terminal prompt. The app shell listens for it. Neither speaks a reply aloud yet; replies are printed or shown in the panel.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

199 tests, all offline: a `Fake` platform records calls and scripted Jev answers stand in for the API. One test launches TextEdit and only runs with `ROCKY_LIVE=1`. CI runs ruff on Ubuntu and pytest on macOS and Windows runners; the Windows job exercises the mocked UIA walk, which is why the platform still counts as untested. Node is not needed. There is no build step. Pre-commit is optional: `uv run --with pre-commit pre-commit install`.

## Credits

Rocky combines ideas and, in places, adapted code from three MIT projects. See `THIRD_PARTY_NOTICES.md`.

- [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast): the one-request operation-plus-target loop.
- [kevinbadi/jev-voice](https://github.com/kevinbadi/jev-voice): the voice front end, the addressed gate, and select-instead-of-generate text spans.
- [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use): dry run by default, confidence floors, stall detection, replayable run folders, and the rule that platform calls live in one place.

Built by Yousef Qasem in Kuwait. GitHub: [BenjisCollector](https://github.com/BenjisCollector).

## License

MIT. See `LICENSE`.
