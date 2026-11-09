-- Tables of yai-wes (design/wes.md section 5), kept language-independent. No Passports here (memory only, C-18)
CREATE TABLE IF NOT EXISTS runs (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,     -- creation sequence (page tokens, paging.yaml)
  id TEXT NOT NULL UNIQUE,
  owner_iss TEXT NOT NULL,
  owner_sub TEXT NOT NULL,
  state TEXT NOT NULL,
  request_json TEXT NOT NULL,                -- RunRequest (without yai_passports)
  needs_passport INTEGER NOT NULL DEFAULT 0, -- whether resolving inputs needs the Passport held in memory
  created_at INTEGER NOT NULL,
  start_time INTEGER,
  end_time INTEGER,
  outputs_json TEXT,
  system_logs_json TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS steps (
  run_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  name TEXT NOT NULL,
  phase TEXT NOT NULL,                       -- planned | submitting | submitted
  submit_nonce TEXT,
  tes_task_id TEXT,
  state TEXT,                                -- TES state (last read)
  cmd_json TEXT NOT NULL DEFAULT '[]',
  exit_code INTEGER,
  start_time INTEGER,
  end_time INTEGER,
  cancel_sent_at INTEGER,
  unreachable_since INTEGER,                 -- since when TES is unreachable or UNKNOWN (counted across restarts)
  cancel_pending INTEGER NOT NULL DEFAULT 0, -- keep sending CancelTask after the run ended (wes.md section 4)
  outputs_json TEXT,                         -- outputs recorded when a step is COMPLETE (inputs of the next step of a sequential Workflow)
  PRIMARY KEY (run_id, n)
);
