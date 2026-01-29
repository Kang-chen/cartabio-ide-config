# Global Rules

## Language & Writing

- Reply language: match user's input (Chinese → Chinese)
- Comments: English; Technical terms: keep original English
- Code identifiers/strings/logs: English
- No AI attribution in commits
- Keep user's voice, conversational, no em dashes, fix minor grammar only

## Coding Standards

### Python
1. Use Python 3.10+ syntax
2. Always use Type Hints
3. Follow PEP 8 style guide
4. Prefer functional programming patterns when appropriate
5. Use meaningful variable names

### General
1. Write clean, readable code
2. Add comments for complex logic
3. Keep functions small and focused
4. DRY (Don't Repeat Yourself)

## Communication Style

1. Be concise and direct
2. Do not apologize for errors, just fix them
3. Explain reasoning when making decisions
4. Ask for clarification when requirements are unclear
5. Provide working code, not just explanations
6. When debugging, show the root cause first

## Shell Commands & Long-running Jobs

For a complex bash command, either run it as multiple individual commands, or put it in a bash script file and run it with `bash /tmp/<script>.sh`.

### Tmux Sessions
For long-running job sessions:

```bash
tmux new-session -d -s <name> '<command>'
tmux send-keys -t <name> '<input>' Enter  # don't forget Enter!
tmux capture-pane -t <name> -p
```

Note: You may need to send Enter again after a short delay to ensure the prompt is submitted.

### Waiting for Jobs
If you need to wait for a long-running job, use sleep commands with manual exponential backoff: wait 1 minute, then 2 minutes, then 4 minutes, and so on.
