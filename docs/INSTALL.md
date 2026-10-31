# Installing ContextBridge (macOS, beginner-friendly)

Everything runs on your Mac. Nothing is sent anywhere, and no API key is needed. Commands go in
**Terminal** (press ⌘-Space, type "Terminal", press Return). Copy each command, paste it, press
Return. You can also run them inside Claude Code by typing `!` first (for example `! ctxb doctor`).

## 1. Prerequisites (one time)

| What | Check | Install if missing |
|---|---|---|
| Homebrew | `brew --version` | see https://brew.sh |
| uv (Python tool installer) | `uv --version` | `brew install uv` |
| git (only for repo details in handoffs) | `git --version` | `xcode-select --install` |

uv downloads the Python version ContextBridge needs (3.14) automatically.

## 2. Install the program

```bash
git clone https://github.com/aryansingh4161-cmd/contextbridge.git ~/contextbridge
uv tool install ~/contextbridge
```

- The first line downloads the code into a `contextbridge` folder in your home folder.
- The second installs two commands, `ctxb` and `contextbridge-mcp`, into `~/.local/bin`. They are
  installed into their own private Python environment, so they don't affect anything else.
- Don't install from (or keep the code in) **Desktop** or **Documents** if those are synced with iCloud: iCloud can
  hide files Python needs, and the server then fails to start. The installer warns you if it notices this.

Check it worked:

```bash
ctxb version        # prints: ctxb 0.1.0
ctxb init           # creates ~/.contextbridge (your memory: database, backups, exports)
ctxb doctor         # health check; everything should be ✓ or ·
```

If `ctxb: command not found`: run `uv tool update-shell`, close Terminal, open it again.

To update later: `cd ~/contextbridge && git pull && uv tool install --reinstall ~/contextbridge`.

## 3. Connect Claude Code

```bash
ctxb install-claude-code --dry-run   # shows what it would change; changes nothing
ctxb install-claude-code
```

This does two things, and backs up each file it touches first (into `~/.contextbridge/backups/client-configs/`):

1. Runs `claude mcp add --transport stdio --scope user contextbridge -- ~/.local/bin/contextbridge-mcp`
   so ContextBridge is available in **every** project (stored by Claude Code in `~/.claude.json`).
2. Adds a **SessionStart hook** to `~/.claude/settings.json`. When you start Claude Code in a folder
   that you've linked to a project (step 5), the hook loads that project's brief context
   automatically. In other folders it does nothing. Your existing settings and hooks are kept.
   Use `--no-hook` to skip this part.

Check: run `claude mcp list` — you should see `contextbridge: … ✓ Connected`.

## 4. Connect Claude Desktop

```bash
ctxb install-claude-desktop --dry-run
ctxb install-claude-desktop
```

This adds a `contextbridge` entry under `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json` (backup first; your other settings
and servers are kept). Then **quit Claude Desktop completely** (menu Claude → Quit Claude, or ⌘Q —
closing the window is not enough) and open it again.

Check: in a new chat, click the **+** / tools (connectors) button below the message box; "contextbridge"
should be listed with its tools. Or simply ask: "List my ContextBridge projects."

**Switching Claude accounts:** the config is a file on your Mac, not part of your account, and your
memory is in `~/.contextbridge`. Logging out of one account and into another on the same Mac keeps
both. *(We verify this in M7 with you — see PROGRESS.md.)*

## 5. Start using it

- Ask Claude: *"Create a ContextBridge project called My App: <one sentence about it>."*
- Link the project to its code folder (for Claude Code's automatic context and repo details in handoffs):
  `ctxb link my-app ~/code/my-app`
- Teach the habit (optional but recommended):
  - Claude Code: `ctxb snippet claude-md my-app >> ~/code/my-app/CLAUDE.md`
  - Claude Desktop / claude.ai Project: `ctxb snippet desktop my-app | pbcopy`, then paste into the
    Project's **instructions**.
- When you're switching accounts or stopping: *"Prepare a complete handoff, I'm switching accounts."*
- In a fresh chat: *"Load my My App context and continue where I left off."*

## 6. Uninstall

```bash
ctxb install-claude-code --uninstall
ctxb install-claude-desktop --uninstall
uv tool uninstall contextbridge
```

Your memory stays in `~/.contextbridge` until you delete that folder yourself
(`ctxb export --full` first if you want to keep a copy).

## Other clients

| Client | Works today? | How |
|---|---|---|
| Claude Code | yes | step 3 |
| Claude Desktop (macOS) | yes | step 4 |
| claude.ai in a browser, ChatGPT | not directly: they only connect to **remote HTTPS** MCP servers, and ContextBridge is local-only by design in this version | attach the handoff file (`01_START_HERE_handoff.txt`) to the chat |
| Any other MCP client with local (stdio) servers | probably | point it at `~/.local/bin/contextbridge-mcp` |
