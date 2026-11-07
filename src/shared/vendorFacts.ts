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
    "login_args": null,
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": null,
    "session_root": null,
    "session_path": null,
    "logs": null,
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [],
      "home": [
        ".gemini",
        "antigravity-cli"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [],
      "home": [
        ".gemini",
        "antigravity-cli"
      ],
      "fallbacks": [],
      "sub": [
        "conversations"
      ]
    },
    "session_path": {
      "segments": [
        "{id}.db"
      ],
      "cwd": "absent"
    },
    "logs": {
      "files": [
        "*.db"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": "auth login",
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "CLAUDE_CONFIG_DIR"
      ],
      "home": [
        ".claude"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "CLAUDE_CONFIG_DIR"
      ],
      "home": [
        ".claude"
      ],
      "fallbacks": [
        ".config/claude"
      ],
      "sub": [
        "projects"
      ]
    },
    "session_path": {
      "segments": [
        "{cwd}",
        "{id}.jsonl"
      ],
      "cwd": "claude"
    },
    "logs": {
      "files": [
        "{cwd}/*.jsonl"
      ],
      "cwd": "claude"
    },
    "permission_aliases": {
      "spellings": [
        "--allow-dangerously-skip-permissions",
        "--permission-mode bypassPermissions"
      ],
      "mode_flags": [
        "--permission-mode"
      ],
      "config_keys": []
    }
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
    "login_args": "login",
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "CODEX_HOME"
      ],
      "home": [
        ".codex"
      ],
      "append": [],
      "extra_env": [
        "CODEX_SQLITE_HOME"
      ],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [],
      "home": [
        ".codex"
      ],
      "fallbacks": [],
      "sub": [
        "sessions"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "**/rollout-*.jsonl"
      ],
      "cwd": "absent"
    },
    "permission_aliases": {
      "spellings": [
        "--yolo",
        "--full-auto"
      ],
      "mode_flags": [
        "-a",
        "--ask-for-approval",
        "-s",
        "--sandbox"
      ],
      "config_keys": [
        "approval_policy",
        "sandbox_mode"
      ]
    }
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
    "login_args": "login",
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "COPILOT_HOME"
      ],
      "home": [
        ".copilot"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "COPILOT_HOME"
      ],
      "home": [
        ".copilot"
      ],
      "fallbacks": [],
      "sub": []
    },
    "session_path": {
      "segments": [
        "session-state",
        "{id}",
        "events.jsonl"
      ],
      "cwd": "absent"
    },
    "logs": {
      "files": [
        "session-store.db",
        "session-state/*/events.jsonl"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": {
      "programs": [
        "cursor-agent"
      ],
      "flags": []
    },
    "subcommand": null,
    "data_dirs": {
      "env": [],
      "home": [
        ".cursor",
        "chats"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [],
      "home": [
        ".cursor",
        "chats"
      ],
      "fallbacks": [],
      "sub": []
    },
    "session_path": null,
    "logs": {
      "files": [
        "{cwd}/*/store.db"
      ],
      "cwd": "md5"
    },
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": {
      "programs": [],
      "flags": [
        "-r"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "FACTORY_HOME_OVERRIDE"
      ],
      "home": [
        ".factory"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "FACTORY_HOME_OVERRIDE"
      ],
      "home": [
        ".factory"
      ],
      "fallbacks": [],
      "sub": [
        "sessions"
      ]
    },
    "session_path": {
      "segments": [
        "{cwd}",
        "{id}.jsonl"
      ],
      "cwd": "droid"
    },
    "logs": {
      "files": [
        "*.jsonl",
        "{cwd}/*.jsonl"
      ],
      "cwd": "droid"
    },
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": null,
    "subcommand": {
      "word": "session",
      "aliases": [
        "s"
      ]
    },
    "data_dirs": null,
    "session_root": null,
    "session_path": null,
    "logs": {
      "files": [
        "sessions.db"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": "login",
    "resume_aliases": {
      "programs": [],
      "flags": [
        "--resume"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "GROK_HOME"
      ],
      "home": [
        ".grok"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "GROK_HOME"
      ],
      "home": [
        ".grok"
      ],
      "fallbacks": [],
      "sub": [
        "sessions"
      ]
    },
    "session_path": {
      "segments": [
        "{cwd}",
        "{id}",
        "updates.jsonl"
      ],
      "cwd": "url_quote"
    },
    "logs": {
      "files": [
        "{cwd}/*/updates.jsonl"
      ],
      "cwd": "url_quote"
    },
    "permission_aliases": null
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
    "login_args": "auth login",
    "resume_aliases": {
      "programs": [],
      "flags": [
        "-s"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "append": [
        "kilo"
      ],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "fallbacks": [],
      "sub": [
        "kilo"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "kilo.db"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": "login",
    "resume_aliases": {
      "programs": [],
      "flags": [
        "-S"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "KIMI_CODE_HOME"
      ],
      "home": [
        ".kimi-code"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "KIMI_CODE_HOME"
      ],
      "home": [
        ".kimi-code"
      ],
      "fallbacks": [],
      "sub": [
        "sessions"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "wd_*/session_*/agents/main/wire.jsonl"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": "login",
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "MINIMAX_DATA_DIR",
        "MAVIS_DATA_DIR"
      ],
      "home": [
        ".minimax"
      ],
      "append": [],
      "extra_env": [],
      "tilde": false
    },
    "session_root": null,
    "session_path": null,
    "logs": null,
    "permission_aliases": null
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
    "login_args": "login",
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "append": [
        "muse"
      ],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "fallbacks": [],
      "sub": [
        "muse",
        "sessions"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "*/*/*/*/session.jsonl"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": "auth login",
    "resume_aliases": {
      "programs": [],
      "flags": [
        "-s"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "append": [
        "opencode"
      ],
      "extra_env": [],
      "tilde": false
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "XDG_DATA_HOME"
      ],
      "home": [
        ".local",
        "share"
      ],
      "fallbacks": [],
      "sub": [
        "opencode"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "opencode.db"
      ],
      "cwd": "absent"
    },
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": null,
    "subcommand": null,
    "data_dirs": {
      "env": [
        "PI_CODING_AGENT_DIR"
      ],
      "home": [
        ".pi",
        "agent"
      ],
      "append": [],
      "extra_env": [
        "PI_CODING_AGENT_SESSION_DIR"
      ],
      "tilde": false
    },
    "session_root": {
      "root_env": [
        "PI_CODING_AGENT_SESSION_DIR"
      ],
      "base_env": [
        "PI_CODING_AGENT_DIR"
      ],
      "home": [
        ".pi",
        "agent"
      ],
      "fallbacks": [],
      "sub": [
        "sessions"
      ]
    },
    "session_path": null,
    "logs": {
      "files": [
        "{cwd}/*.jsonl"
      ],
      "cwd": "pi"
    },
    "permission_aliases": null
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
    "login_args": null,
    "resume_aliases": {
      "programs": [],
      "flags": [
        "-r"
      ]
    },
    "subcommand": null,
    "data_dirs": {
      "env": [
        "QWEN_HOME"
      ],
      "home": [
        ".qwen"
      ],
      "append": [],
      "extra_env": [
        "QWEN_RUNTIME_DIR"
      ],
      "tilde": true
    },
    "session_root": {
      "root_env": [],
      "base_env": [
        "QWEN_RUNTIME_DIR"
      ],
      "home": [
        ".qwen"
      ],
      "fallbacks": [],
      "sub": [
        "projects"
      ]
    },
    "session_path": {
      "segments": [
        "{cwd}",
        "chats",
        "{id}.jsonl"
      ],
      "cwd": "claude"
    },
    "logs": {
      "files": [
        "{cwd}/chats/*.jsonl",
        "{cwd}/chats/archive/*.jsonl"
      ],
      "cwd": "claude"
    },
    "permission_aliases": null
  }
} as const

/** The option table: every launch option a word can name, and how it is spelled from a vendor's facts. */
export const OPTION_RULES = {
  "model": {
    "field": "model_flag",
    "takes_value": true,
    "template": false,
    "values": null
  },
  "effort": {
    "field": "effort_flag",
    "takes_value": true,
    "template": false,
    "values": "known_efforts"
  },
  "skip_permissions": {
    "field": "permission_flag",
    "takes_value": false,
    "template": false,
    "values": null
  },
  "resume": {
    "field": "resume_args",
    "takes_value": true,
    "template": true,
    "values": null
  },
  "session_id": {
    "field": "session_id_flag",
    "takes_value": true,
    "template": false,
    "values": null
  },
  "login": {
    "field": "login_args",
    "takes_value": false,
    "template": false,
    "values": null
  }
} as const

export type LaunchOption = keyof typeof OPTION_RULES

/** How each vendor's session log is observed (null where none is). */
export const OBSERVER_FACTS = {
  "aider": {
    "parser": "aider",
    "parser_id": "rust-aider-md-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "text",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd",
          "namespace"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/kind",
              "equals": "tokens"
            }
          ],
          "object": null,
          "input": [
            "/input"
          ],
          "output": [
            "/output"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": null,
          "version": null,
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "string",
          "session": "/session",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": false,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Aider observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "aider_text::",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "aider_walk::",
          "field": "adapter",
          "kind": "json"
        }
      ],
      "python": {
        "prompt_detail": "prompt",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "antigravity": {
    "parser": "antigravity",
    "parser_id": "rust-antigravity-sqlite-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "sqlite",
        "usage": "none",
        "store_file": null,
        "request": [
          "cwd",
          "session"
        ],
        "usage_fields": null,
        "store": {
          "parent": null,
          "marks": [
            {
              "table": "steps",
              "id": "idx",
              "key": "idx",
              "payload": "step_payload",
              "columns": [
                "step_type",
                "status",
                "metadata"
              ],
              "prefix": "step:",
              "key_scope": "session",
              "start": "newest",
              "pending_max_age": {
                "pointer": null,
                "unit": "seconds",
                "seconds": 86400
              },
              "shrink": "keep",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "after": null,
              "group": null,
              "owner": null,
              "stamp": null
            }
          ],
          "marker": null,
          "rows": null,
          "row_prefix": null
        },
        "rules": null,
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Antigravity observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "agy_idx::",
          "field": "marks.steps.last",
          "kind": "signed"
        },
        {
          "prefix": "agy_pending::",
          "field": "marks.steps.pending",
          "kind": "set"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "claude": {
    "parser": "claude",
    "parser_id": "rust-claude-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "assistant"
            }
          ],
          "object": "/message/usage",
          "input": [
            "/message/usage/input_tokens"
          ],
          "output": [
            "/message/usage/output_tokens"
          ],
          "cache_read": [
            "/message/usage/cache_read_input_tokens"
          ],
          "cache_creation": [
            "/message/usage/cache_creation_input_tokens"
          ],
          "key": [
            "/message/id",
            "/requestId"
          ],
          "key_join": "::",
          "model": "/message/model",
          "version": "/version",
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": true,
          "non_object_fails": true,
          "activity_non_object_fails": true,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "request": [],
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity",
        "turns"
      ],
      "messages": {
        "unsupported_mode": "invalid observation mode",
        "non_object": "Claude record is not an object"
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "file_stem",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "codex": {
    "parser": "codex",
    "parser_id": "rust-codex-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "cumulative",
        "store_file": null,
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "event_msg"
            },
            {
              "pointer": "/payload/type",
              "equals": "token_count"
            }
          ],
          "object": "/payload/info/total_token_usage",
          "input": [
            "/payload/info/total_token_usage/input_tokens",
            "/payload/info/total_token_usage/cached_input_tokens"
          ],
          "output": [
            "/payload/info/total_token_usage/output_tokens",
            "/payload/info/total_token_usage/reasoning_output_tokens"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": null,
          "version": null,
          "timestamp": "/timestamp",
          "number": "strict",
          "text": "string",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": false,
          "invalid_utf8_fails": false,
          "non_object_fails": false,
          "activity_non_object_fails": true,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "request": [],
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity",
        "identify"
      ],
      "messages": {
        "unsupported_mode": "Codex observation mode not integrated",
        "non_object": "Codex record is not an object"
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "__lasttext__:",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "__cum__:",
          "field": "totals",
          "kind": "pair"
        }
      ],
      "python": {
        "prompt_detail": "user_message",
        "pane_scope": "home_dir",
        "attribution": "none",
        "identity_source": "header"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": true,
        "store_error_reported": false
      }
    }
  },
  "copilot": {
    "parser": "copilot",
    "parser_id": "rust-copilot-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "session-store.db",
        "usage_fields": {
          "select": [],
          "object": null,
          "input": [
            "/input_tokens"
          ],
          "output": [
            "/output_tokens"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": "/created_at",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "assistant_usage_events",
            "id": "id",
            "key": "id",
            "group": "session_id",
            "columns": [
              "model",
              "input_tokens",
              "output_tokens",
              "created_at"
            ],
            "anchor": [
              "session_id",
              "model",
              "input_tokens",
              "output_tokens",
              "created_at"
            ],
            "payload": null,
            "pending_until": null,
            "pending_max_age": null,
            "cursor_tag": null,
            "raw_id": null
          },
          "parent": {
            "table": "sessions",
            "key": "id",
            "dir": "cwd",
            "join": "left",
            "up": null,
            "version": null,
            "columns": [],
            "top": null,
            "dir_form": "as_stored"
          },
          "marks": [
            {
              "table": "assistant_usage_events",
              "id": "id",
              "group": "session_id",
              "columns": [
                "created_at"
              ],
              "prefix": "db_act:usage:",
              "start": "oldest",
              "shrink": "oldest",
              "key": null,
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "after": null,
              "owner": null,
              "payload": null,
              "stamp": null
            },
            {
              "table": "turns",
              "id": "id",
              "group": "session_id",
              "columns": [
                "user_message",
                "assistant_response",
                "timestamp"
              ],
              "prefix": "db_act:turn:",
              "start": "oldest",
              "shrink": "oldest",
              "after": "assistant_usage_events",
              "key": null,
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "owner": null,
              "payload": null,
              "stamp": null
            }
          ],
          "row_prefix": "copilot_usage::",
          "marker": null
        },
        "rules": null,
        "defers_to": null,
        "request": [],
        "applies_when": []
      },
      {
        "source": "jsonl",
        "usage": "cumulative",
        "store_file": "events.jsonl",
        "request": [
          "cwd",
          "session"
        ],
        "usage_fields": {
          "select": [],
          "object": "/data/tokenDetails",
          "input": [
            "/data/tokenDetails/input/tokenCount",
            "/data/tokenDetails/cache_read/tokenCount",
            "/data/tokenDetails/cache_write/tokenCount"
          ],
          "output": [
            "/data/tokenDetails/output/tokenCount"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": null,
          "version": null,
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": false,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": true,
          "water_survives_replacement": true
        },
        "defers_to": {
          "file": "session-store.db",
          "up": 3
        },
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Copilot observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "__lasttext__:",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "__cum__:",
          "field": "totals",
          "kind": "pair"
        },
        {
          "prefix": "db_act_hw::usage:",
          "field": "marks.assistant_usage_events.last",
          "kind": "int"
        },
        {
          "prefix": "db_act_hw::turn:",
          "field": "marks.turns.last",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": true
      }
    }
  },
  "cursor": {
    "parser": "cursor",
    "parser_id": "rust-cursor-sqlite-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "sqlite",
        "usage": "none",
        "store_file": "store.db",
        "request": [
          "cwd",
          "session",
          "base_epoch"
        ],
        "usage_fields": null,
        "store": {
          "parent": null,
          "marks": [
            {
              "table": "blobs",
              "id": "rowid",
              "key": "id",
              "payload": "data",
              "prefix": "blob:",
              "page": 512,
              "text_cap": 8000,
              "start": "newest_or_zero",
              "shrink": "newest",
              "columns": [],
              "pending_max_age": null,
              "key_scope": "none",
              "reply_from": null,
              "after": null,
              "group": null,
              "owner": null,
              "stamp": null
            }
          ],
          "marker": {
            "text": "at-pane:",
            "on": "blobs",
            "within": {
              "rows": 512,
              "bytes": 262144
            },
            "binding": "path_session",
            "role": null,
            "order_by_stamp": false,
            "pointer": null
          },
          "rows": null,
          "row_prefix": null
        },
        "rules": null,
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity",
        "identify"
      ],
      "messages": {
        "unsupported_mode": "Cursor observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "cursor_blob::",
          "field": "marks.blobs.last",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "store"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "droid": {
    "parser": "droid",
    "parser_id": "rust-droid-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "cumulative",
        "store_file": null,
        "request": [
          "cwd",
          "session"
        ],
        "usage_fields": {
          "select": [],
          "object": "/tokenUsage",
          "input": [
            "/tokenUsage/inputTokens",
            "/tokenUsage/cacheCreationTokens",
            "/tokenUsage/cacheReadTokens"
          ],
          "output": [
            "/tokenUsage/outputTokens"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": null,
          "number": "lenient",
          "text": "any",
          "document": ".settings.json",
          "cache": "folded",
          "time": "text",
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": false,
          "invalid_utf8_fails": true,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Droid observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "droid_text::",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "droid_tok::",
          "field": "totals",
          "kind": "colon_pair"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "goose": {
    "parser": "goose",
    "parser_id": "rust-goose-sqlite-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "sessions.db",
        "usage_fields": {
          "select": [],
          "object": null,
          "input": [
            "/input_tokens"
          ],
          "output": [
            "/output_tokens"
          ],
          "cache_read": [
            "/cache_read_tokens"
          ],
          "cache_creation": [
            "/cache_write_tokens"
          ],
          "key": [],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": "/created_timestamp",
          "number": "lenient",
          "text": "any",
          "cache": "separate",
          "time": "epoch_seconds",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "usage_ledger",
            "id": "id",
            "key": "id",
            "group": "session_id",
            "columns": [
              "model",
              "created_timestamp",
              "input_tokens",
              "output_tokens",
              "cache_read_tokens",
              "cache_write_tokens"
            ],
            "raw_id": "row_id",
            "payload": null,
            "pending_until": null,
            "pending_max_age": null,
            "cursor_tag": null,
            "anchor": [
              "id",
              "session_id"
            ]
          },
          "parent": {
            "table": "sessions",
            "key": "id",
            "dir": "working_dir",
            "up": "parent_session_id",
            "columns": [
              "session_type"
            ],
            "top": {
              "column": "session_type",
              "equals": "user"
            },
            "version": null,
            "join": "inner",
            "dir_form": "as_stored"
          },
          "marks": [
            {
              "table": "messages",
              "id": "id",
              "group": "session_id",
              "payload": "content_json",
              "stamp": "created_timestamp",
              "columns": [
                "role",
                "metadata_json"
              ],
              "prefix": "message:",
              "start": "newest_or_zero",
              "key": null,
              "shrink": "keep",
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "after": null,
              "owner": null
            }
          ],
          "row_prefix": "usage:",
          "marker": {
            "text": "at-pane:",
            "role": {
              "column": "role",
              "equals": "user"
            },
            "on": "messages",
            "order_by_stamp": false,
            "within": null,
            "binding": "store_rows",
            "pointer": null
          }
        },
        "rules": null,
        "defers_to": null,
        "request": [],
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity",
        "has_session",
        "identify"
      ],
      "messages": {
        "unsupported_mode": "Goose observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "goose_message::",
          "field": "marks.messages.last",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "store"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "grok": {
    "parser": "grok",
    "parser_id": "rust-grok-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/kind",
              "equals": "turn_completed"
            }
          ],
          "object": "/usage",
          "input": [
            "/usage/inputTokens"
          ],
          "output": [
            "/usage/outputTokens"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "string",
          "session": "/session",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": false,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": false,
          "water_survives_replacement": false
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity",
        "turns"
      ],
      "messages": {
        "unsupported_mode": "Grok observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "grok_text::",
          "field": "last_text",
          "kind": "json_text"
        },
        {
          "prefix": "grok_act::",
          "field": "legacy_cursor",
          "kind": "byte_cursor"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "kilo": {
    "parser": "opencode",
    "parser_id": "rust-kilo-sqlite-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "applies_when": [
          "session_v2",
          "session_message"
        ],
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "kilo.db",
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "assistant"
            }
          ],
          "object": null,
          "input": [
            "/tokens/input",
            "/tokens/cache/read",
            "/tokens/cache/write"
          ],
          "output": [
            "/tokens/output",
            "/tokens/reasoning"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model/id",
          "version": null,
          "timestamp": "/time/completed",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "session_message",
            "id": "rowid",
            "key": "id",
            "group": "session_id",
            "payload": "data",
            "columns": [
              "type"
            ],
            "pending_until": "/time/completed",
            "pending_max_age": {
              "pointer": "/time/created",
              "unit": "millis",
              "seconds": 86400
            },
            "cursor_tag": "v2",
            "raw_id": null,
            "anchor": [
              "id",
              "session_id"
            ]
          },
          "parent": {
            "table": "session_v2",
            "key": "id",
            "dir": "directory",
            "up": "parent_id",
            "version": "version",
            "dir_form": "windows_native",
            "columns": [],
            "join": "inner",
            "top": null
          },
          "marks": [
            {
              "table": "session_message",
              "id": "rowid",
              "group": "session_id",
              "payload": "data",
              "stamp": "time_created",
              "columns": [
                "type",
                "seq"
              ],
              "prefix": "message:",
              "start": "newest_or_zero",
              "reply_from": {
                "order": "seq",
                "where": {
                  "column": "type",
                  "equals": "assistant"
                },
                "before": "seq"
              },
              "key": null,
              "shrink": "keep",
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "after": null,
              "owner": null
            }
          ],
          "row_prefix": "msg:",
          "marker": {
            "text": "at-pane:",
            "role": {
              "column": "type",
              "equals": "user"
            },
            "order_by_stamp": true,
            "pointer": "/text",
            "on": "session_message",
            "within": null,
            "binding": "store_rows"
          }
        },
        "rules": null,
        "defers_to": null,
        "request": []
      },
      {
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "kilo.db",
        "usage_fields": {
          "select": [
            {
              "pointer": "/role",
              "equals": "assistant"
            }
          ],
          "object": null,
          "input": [
            "/tokens/input",
            "/tokens/cache/read",
            "/tokens/cache/write"
          ],
          "output": [
            "/tokens/output",
            "/tokens/reasoning"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/modelID",
          "version": null,
          "timestamp": "/time/completed",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "message",
            "id": "rowid",
            "key": "id",
            "group": "session_id",
            "payload": "data",
            "pending_until": "/time/completed",
            "pending_max_age": {
              "pointer": "/time/created",
              "unit": "millis",
              "seconds": 86400
            },
            "columns": [],
            "cursor_tag": null,
            "raw_id": null,
            "anchor": [
              "id",
              "session_id"
            ]
          },
          "parent": {
            "table": "session",
            "key": "id",
            "dir": "directory",
            "up": "parent_id",
            "version": "version",
            "columns": [],
            "join": "inner",
            "top": null,
            "dir_form": "as_stored"
          },
          "marks": [
            {
              "table": "part",
              "id": "rowid",
              "group": "session_id",
              "owner": "message_id",
              "payload": "data",
              "stamp": "time_created",
              "prefix": "part:",
              "key": null,
              "columns": [],
              "start": "newest",
              "shrink": "keep",
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "after": null
            }
          ],
          "row_prefix": "msg:",
          "marker": {
            "text": "at-pane:",
            "role": {
              "pointer": "/role",
              "equals": "user"
            },
            "order_by_stamp": true,
            "on": "part",
            "within": null,
            "binding": "store_rows",
            "pointer": null
          }
        },
        "rules": null,
        "defers_to": null,
        "request": [],
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "legacy",
        "activity",
        "has_session",
        "identify"
      ],
      "messages": {
        "unsupported_mode": "OpenCode observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "opencode_part::",
          "field": "marks.part.last",
          "kind": "int"
        },
        {
          "prefix": "opencode_message::",
          "field": "marks.session_message.last",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "store"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "kimi": {
    "parser": "kimi",
    "parser_id": "rust-kimi-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd",
          "session",
          "now_ms"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "usage.record"
            }
          ],
          "object": "/usage",
          "input": [
            "/usage/inputOther",
            "/usage/inputCacheRead",
            "/usage/inputCacheCreation"
          ],
          "output": [
            "/usage/output"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": "/time",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": true,
          "non_object_fails": true,
          "activity_non_object_fails": true,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Kimi observation mode not integrated",
        "non_object": "Kimi record is not an object"
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "kimi_text::",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "kimi_turn::",
          "field": "adapter",
          "kind": "json"
        }
      ],
      "python": {
        "prompt_detail": "prompt",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "mcode": null,
  "muse": {
    "parser": "muse",
    "parser_id": "rust-muse-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd",
          "session",
          "cli_version"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/payload_type",
              "equals": "runtime.session"
            },
            {
              "pointer": "/payload/event/kind",
              "equals": "goal_usage_attribution"
            },
            {
              "pointer": "/payload/event/record/owner/requester_kind",
              "equals": "main"
            }
          ],
          "object": "/payload/event/record/quantity",
          "input": [
            "/payload/event/record/quantity/input_tokens"
          ],
          "output": [
            "/payload/event/record/quantity/output_tokens"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [
            "/payload/event/record/usage_id"
          ],
          "key_join": "",
          "model": null,
          "version": null,
          "timestamp": "/recorded_at",
          "number": "lenient",
          "text": "string",
          "time": "epoch_micros",
          "cache": "folded",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": true,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Muse observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "muse_text::",
          "field": "last_text",
          "kind": "text"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "opencode": {
    "parser": "opencode",
    "parser_id": "rust-opencode-sqlite-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "applies_when": [
          "session_v2",
          "session_message"
        ],
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "opencode.db",
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "assistant"
            }
          ],
          "object": null,
          "input": [
            "/tokens/input",
            "/tokens/cache/read",
            "/tokens/cache/write"
          ],
          "output": [
            "/tokens/output",
            "/tokens/reasoning"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/model/id",
          "version": null,
          "timestamp": "/time/completed",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "session_message",
            "id": "rowid",
            "key": "id",
            "group": "session_id",
            "payload": "data",
            "columns": [
              "type"
            ],
            "pending_until": "/time/completed",
            "pending_max_age": {
              "pointer": "/time/created",
              "unit": "millis",
              "seconds": 86400
            },
            "cursor_tag": "v2",
            "raw_id": null,
            "anchor": [
              "id",
              "session_id"
            ]
          },
          "parent": {
            "table": "session_v2",
            "key": "id",
            "dir": "directory",
            "up": "parent_id",
            "version": "version",
            "dir_form": "windows_native",
            "columns": [],
            "join": "inner",
            "top": null
          },
          "marks": [
            {
              "table": "session_message",
              "id": "rowid",
              "group": "session_id",
              "payload": "data",
              "stamp": "time_created",
              "columns": [
                "type",
                "seq"
              ],
              "prefix": "message:",
              "start": "newest_or_zero",
              "reply_from": {
                "order": "seq",
                "where": {
                  "column": "type",
                  "equals": "assistant"
                },
                "before": "seq"
              },
              "key": null,
              "shrink": "keep",
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "after": null,
              "owner": null
            }
          ],
          "row_prefix": "msg:",
          "marker": {
            "text": "at-pane:",
            "role": {
              "column": "type",
              "equals": "user"
            },
            "order_by_stamp": true,
            "pointer": "/text",
            "on": "session_message",
            "within": null,
            "binding": "store_rows"
          }
        },
        "rules": null,
        "defers_to": null,
        "request": []
      },
      {
        "source": "sqlite",
        "usage": "per_turn",
        "store_file": "opencode.db",
        "usage_fields": {
          "select": [
            {
              "pointer": "/role",
              "equals": "assistant"
            }
          ],
          "object": null,
          "input": [
            "/tokens/input",
            "/tokens/cache/read",
            "/tokens/cache/write"
          ],
          "output": [
            "/tokens/output",
            "/tokens/reasoning"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [],
          "key_join": "",
          "model": "/modelID",
          "version": null,
          "timestamp": "/time/completed",
          "number": "lenient",
          "text": "any",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": {
          "rows": {
            "table": "message",
            "id": "rowid",
            "key": "id",
            "group": "session_id",
            "payload": "data",
            "pending_until": "/time/completed",
            "pending_max_age": {
              "pointer": "/time/created",
              "unit": "millis",
              "seconds": 86400
            },
            "columns": [],
            "cursor_tag": null,
            "raw_id": null,
            "anchor": [
              "id",
              "session_id"
            ]
          },
          "parent": {
            "table": "session",
            "key": "id",
            "dir": "directory",
            "up": "parent_id",
            "version": "version",
            "columns": [],
            "join": "inner",
            "top": null,
            "dir_form": "as_stored"
          },
          "marks": [
            {
              "table": "part",
              "id": "rowid",
              "group": "session_id",
              "owner": "message_id",
              "payload": "data",
              "stamp": "time_created",
              "prefix": "part:",
              "key": null,
              "columns": [],
              "start": "newest",
              "shrink": "keep",
              "pending_max_age": null,
              "key_scope": "none",
              "page": 256,
              "text_cap": null,
              "reply_from": null,
              "after": null
            }
          ],
          "row_prefix": "msg:",
          "marker": {
            "text": "at-pane:",
            "role": {
              "pointer": "/role",
              "equals": "user"
            },
            "order_by_stamp": true,
            "on": "part",
            "within": null,
            "binding": "store_rows",
            "pointer": null
          }
        },
        "rules": null,
        "defers_to": null,
        "request": [],
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "legacy",
        "activity",
        "has_session",
        "identify"
      ],
      "messages": {
        "unsupported_mode": "OpenCode observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "opencode_part::",
          "field": "marks.part.last",
          "kind": "int"
        },
        {
          "prefix": "opencode_message::",
          "field": "marks.session_message.last",
          "kind": "int"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "store"
      },
      "compat": {
        "absent_file_is_empty": false,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "pi": {
    "parser": "pi",
    "parser_id": "rust-pi-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd",
          "session",
          "now_ms"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/kind",
              "equals": "usage"
            }
          ],
          "object": "/usage",
          "input": [
            "/usage/input",
            "/usage/cacheRead",
            "/usage/cacheWrite"
          ],
          "output": [
            "/usage/output"
          ],
          "cache_read": [],
          "cache_creation": [],
          "key": [
            "/id"
          ],
          "key_join": "",
          "model": "/model",
          "version": null,
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "string",
          "cache": "folded",
          "time": "text",
          "document": null,
          "cwd": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": true,
          "non_object_fails": false,
          "activity_non_object_fails": false,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Pi observation mode not integrated",
        "non_object": null
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "pi_text::",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "pi_turn::",
          "field": "adapter",
          "kind": "json"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  },
  "qwen": {
    "parser": "qwen",
    "parser_id": "rust-qwen-jsonl-v1",
    "platforms": [
      "unix"
    ],
    "layouts": [
      {
        "source": "jsonl",
        "usage": "per_turn",
        "store_file": null,
        "request": [
          "cwd",
          "session",
          "now_ms"
        ],
        "usage_fields": {
          "select": [
            {
              "pointer": "/type",
              "equals": "assistant"
            }
          ],
          "object": "/usageMetadata",
          "input": [
            "/usageMetadata/promptTokenCount"
          ],
          "output": [
            "/usageMetadata/candidatesTokenCount",
            "/usageMetadata/thoughtsTokenCount"
          ],
          "cache_read": [
            "/usageMetadata/cachedContentTokenCount"
          ],
          "cache_creation": [],
          "key": [
            "/uuid"
          ],
          "key_join": "",
          "model": "/model",
          "version": "/version",
          "timestamp": "/timestamp",
          "number": "lenient",
          "text": "any",
          "cache": "separate",
          "cwd": "/cwd",
          "time": "text",
          "document": null,
          "session": null
        },
        "store": null,
        "rules": {
          "missing_identity_rotates": true,
          "invalid_utf8_fails": true,
          "non_object_fails": true,
          "activity_non_object_fails": true,
          "totals_never_decrease": false,
          "water_survives_replacement": true
        },
        "defers_to": null,
        "applies_when": []
      }
    ],
    "core": {
      "modes": [
        "usage",
        "session_usage",
        "activity"
      ],
      "messages": {
        "unsupported_mode": "Qwen observation mode not integrated",
        "non_object": "Qwen record is not an object"
      },
      "legacy_state": [
        {
          "prefix": "act_hw::",
          "field": "water",
          "kind": "int"
        },
        {
          "prefix": "qwen_text::",
          "field": "last_text",
          "kind": "text"
        },
        {
          "prefix": "qwen_turn::",
          "field": "adapter",
          "kind": "json"
        }
      ],
      "python": {
        "prompt_detail": "user",
        "pane_scope": "none",
        "attribution": "none",
        "identity_source": "file_stem"
      },
      "compat": {
        "absent_file_is_empty": true,
        "no_python_turn_fallback": false,
        "store_error_reported": false
      }
    }
  }
} as const

/** Put a session id into a declared `resume_args` template. */
export const fillId = (template: string, id: string): string => template.split('{id}').join(id)
