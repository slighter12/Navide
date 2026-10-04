// Generated from shared/agent-cli/claude.json; do not edit.
export const CLAUDE = {
  "key": "claude",
  "command": "claude",
  "model_flag": "--model",
  "effort_flag": "--effort",
  "known_efforts": [
    "low",
    "medium",
    "high",
    "xhigh",
    "max"
  ],
  "permission_flag": "--dangerously-skip-permissions",
  "resume_flag": "--resume",
  "session_id_flag": "--session-id",
  "login_args": "auth login",
  "shutdown": {
    "graceful": true,
    "grace_s": 3.0,
    "defer_master_close": true
  }
} as const
