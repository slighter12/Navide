// Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit.
export const VENDOR_FACTS = {
  "aider": {
    "key": "aider",
    "command": "aider",
    "model_flag": null,
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--yes-always",
    "resume_args": null,
    "session_id_flag": null,
    "login_args": null
  },
  "antigravity": {
    "key": "antigravity",
    "command": "agy",
    "model_flag": "--model",
    "effort_flag": "--effort",
    "known_efforts": [
      "low",
      "medium",
      "high"
    ],
    "permission_flag": "--dangerously-skip-permissions",
    "resume_args": "--conversation {id}",
    "session_id_flag": null,
    "login_args": null
  },
  "claude": {
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
    "resume_args": "--resume {id}",
    "session_id_flag": "--session-id",
    "login_args": "auth login"
  },
  "codex": {
    "key": "codex",
    "command": "codex",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--dangerously-bypass-approvals-and-sandbox",
    "resume_args": "resume {id}",
    "session_id_flag": null,
    "login_args": "login"
  },
  "copilot": {
    "key": "copilot",
    "command": "copilot",
    "model_flag": "--model",
    "effort_flag": "--reasoning-effort",
    "known_efforts": [
      "none",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "permission_flag": "--yolo",
    "resume_args": "--resume={id}",
    "session_id_flag": null,
    "login_args": "login"
  },
  "cursor": {
    "key": "cursor",
    "command": "agent",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--force",
    "resume_args": "--resume={id}",
    "session_id_flag": null,
    "login_args": null
  },
  "droid": {
    "key": "droid",
    "command": "droid",
    "model_flag": null,
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--auto high",
    "resume_args": "--resume {id}",
    "session_id_flag": null,
    "login_args": null
  },
  "goose": {
    "key": "goose",
    "command": "goose",
    "model_flag": null,
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": null,
    "resume_args": "session -r --session-id {id}",
    "session_id_flag": null,
    "login_args": null
  },
  "grok": {
    "key": "grok",
    "command": "grok",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": null,
    "resume_args": "-r {id}",
    "session_id_flag": "--session-id",
    "login_args": "login"
  },
  "kilo": {
    "key": "kilo",
    "command": "kilo",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--auto",
    "resume_args": "--session {id}",
    "session_id_flag": null,
    "login_args": "auth login"
  },
  "kimi": {
    "key": "kimi",
    "command": "kimi",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--yolo",
    "resume_args": "--session {id}",
    "session_id_flag": null,
    "login_args": "login"
  },
  "mcode": {
    "key": "mcode",
    "command": "mcode",
    "model_flag": null,
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": null,
    "resume_args": null,
    "session_id_flag": null,
    "login_args": "login"
  },
  "muse": {
    "key": "muse",
    "command": "muse",
    "model_flag": "--model",
    "effort_flag": "--reasoning-effort",
    "known_efforts": [
      "none",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
      "ultra"
    ],
    "permission_flag": "--disable-approval",
    "resume_args": "resume {id}",
    "session_id_flag": null,
    "login_args": "login"
  },
  "opencode": {
    "key": "opencode",
    "command": "opencode",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": null,
    "resume_args": "--session {id}",
    "session_id_flag": null,
    "login_args": "auth login"
  },
  "pi": {
    "key": "pi",
    "command": "pi",
    "model_flag": "--model",
    "effort_flag": "--thinking",
    "known_efforts": [
      "off",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "permission_flag": null,
    "resume_args": "--session-id {id}",
    "session_id_flag": null,
    "login_args": null
  },
  "qwen": {
    "key": "qwen",
    "command": "qwen",
    "model_flag": "--model",
    "effort_flag": null,
    "known_efforts": null,
    "permission_flag": "--yolo",
    "resume_args": "--resume {id}",
    "session_id_flag": null,
    "login_args": null
  }
} as const

/** Put a session id into a declared `resume_args` template. */
export const fillId = (template: string, id: string): string => template.split('{id}').join(id)
