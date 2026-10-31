# ContextBridge — User Manual

ContextBridge gives Claude a **memory for your projects** that survives everything that normally
wipes it: a chat getting too long, hitting your usage limit, switching Claude accounts, or moving
between Claude Code and Claude Desktop. Everything stays **on your Mac**: no account, no API key,
nothing uploaded.

---

## Part 1 — Install (about 10 minutes, once)

**You need:** a Mac, and Claude Code and/or the Claude Desktop app.

Open **Terminal** (press ⌘ + Space, type `Terminal`, press Return). Copy each command below, paste
it into Terminal, press Return, and wait for it to finish before the next one.

**Step 1. Install Homebrew** (skip if `brew --version` already works):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
Follow what it prints at the end (it may ask you to run two more lines).

**Step 2. Install uv** (the tool that installs ContextBridge):
```bash
brew install uv
```

**Step 3. Download ContextBridge:**
```bash
git clone https://github.com/aryansingh4161-cmd/contextbridge.git ~/contextbridge
```
> Keep it in your home folder as shown, **not** on the Desktop or in Documents if those sync with
> iCloud.

**Step 4. Install it:**
```bash
uv tool install ~/contextbridge
```
If you see a warning about PATH, run `uv tool update-shell`, then close and reopen Terminal.

**Step 5. Set it up and check it:**
```bash
ctxb init
ctxb doctor
```
`ctxb doctor` should end with **Overall: ok**.

**Step 6. Connect your Claude apps** (do the ones you use):
```bash
ctxb install-claude-code
ctxb install-claude-desktop
```
These add ContextBridge to the apps' settings without touching anything else (a backup is made
first). For Claude Desktop, **quit it completely with ⌘ + Q** and open it again.

**Step 7. Test it.** In Claude, type: `List my ContextBridge projects.`
If a box asks to allow a tool, click **Allow**. Claude should answer that you have no projects yet. ✅

---

## Part 2 — Start a project (once per project)

**Step 1.** Ask Claude:
```
Create a ContextBridge project called My App: <one sentence about what it is>
```
Claude replies with a short name for it (the *slug*), e.g. `my-app`.

**Step 2 (Claude Code users).** Link the project to its folder, so it loads automatically:
```bash
ctxb link my-app ~/path/to/my-app
```
(In Claude Code you can run it by typing `!` first: `! ctxb link my-app ~/path/to/my-app`.)

**Step 3 (optional, Claude Desktop Projects).** Copy the ready-made instructions:
```bash
ctxb snippet desktop my-app | pbcopy
```
and paste them into your Claude Project's **Instructions**.

---

## Part 3 — Everyday use

**Starting work**
- **Claude Code:** open it in the project folder. The project's memory loads by itself, and Claude
  tells you the goal, where you stopped and what's next.
- **Claude Desktop:** say `Load my My App context and continue.`

**While you work**
Just work normally. Claude saves decisions (with the reasons), things you decided *against*, bugs,
fixes and progress as you go, and makes a "checkpoint" every 20–30 minutes.

**When you stop, or switch Claude accounts**
Say:
```
Prepare a complete handoff, I'm switching accounts.
```
Claude saves everything and writes a handoff file. Then, in the new account or a new chat:
```
Load my My App context and continue where I left off.
```

**Using an AI without ContextBridge** (another computer, claude.ai in a browser, ChatGPT)
Attach the handoff file Claude gave you (`01_START_HERE_handoff.md` or `.txt`) to the chat and say
"continue from this".

---

## Part 4 — The status line (Claude Code only)

At the bottom of Claude Code you'll see something like:
```
ctx 40% · 5h 20% · 7d 10% · ckpt 5m ago · handoff ✓ · my-app
```
| Part | Meaning |
|---|---|
| `ctx 40%` | how full the current chat is |
| `5h 20%` / `7d 10%` | your 5-hour and weekly usage limits (paid plans only; `–` until Claude's first reply) |
| `ckpt 5m ago` | when your work was last saved |
| `handoff ✓` | there's still room to make a handoff |

As you approach the limits, Claude is asked to save automatically (once at each level). If a session
ends suddenly, an emergency handoff is written for you.

---

## Part 5 — Useful commands

Run in Terminal (or in Claude Code with `!` in front).

| Command | What it does |
|---|---|
| `ctxb projects` | list your projects |
| `ctxb status my-app` | usage limits, last save, anything unsaved |
| `ctxb handoff my-app` | make a handoff file right now (no AI needed) |
| `ctxb search my-app "login"` | search the project's memory |
| `ctxb doctor` | check that everything is healthy |
| `ctxb export --full` | one backup file of everything (to move to another Mac) |
| `ctxb import --full <file>` | restore that backup on the new Mac |
| `ctxb delete my-app --entry 12 --yes` | delete one saved item (can be undone) |

You can also just ask Claude: *"What do you remember about the login flow?"*, *"Forget entry 12"*,
*"What are the open questions?"*

---

## Part 6 — If something goes wrong

1. Run `ctxb doctor` and read any line marked ✗ or !.
2. **Claude Desktop doesn't see ContextBridge:** quit with **⌘ + Q** (not just closing the window) and reopen.
3. **`ctxb: command not found`:** run `uv tool update-shell`, then reopen Terminal.
4. **The project doesn't load by itself in Claude Code:** link the folder (Part 2, Step 2).
5. More fixes: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

**Update to a new version:**
```bash
cd ~/contextbridge && git pull && uv tool install --reinstall .
```

**Uninstall:**
```bash
ctxb install-claude-code --uninstall
ctxb install-claude-desktop --uninstall
uv tool uninstall contextbridge
```
Your memory stays in `~/.contextbridge` until you delete that folder yourself.

---

**Good to know**
- ContextBridge can't stop a usage limit from interrupting you; it makes sure your work is saved
  when it happens, and tells you honestly what was saved and what wasn't.
- Claude's own suggestions stay marked "unconfirmed" until you agree with them.
- Imported files are treated as information, never as instructions.
