-- Tables of yai-tes (design/tes.md section 5), kept language-independent
CREATE TABLE IF NOT EXISTS tasks (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,     -- creation sequence (page tokens, paging.yaml)
  id TEXT NOT NULL UNIQUE,
  owner_iss TEXT NOT NULL,
  owner_sub TEXT NOT NULL,
  actor TEXT,                                -- service that submitted for the owner (<iss>|<act.sub> of the AT, D-067)
  run_id TEXT,                               -- yai.run_id of tags
  submit_nonce TEXT,                         -- yai.submit_nonce of tags (no duplicates on resubmission)
  state TEXT NOT NULL,
  name TEXT,
  created_at INTEGER NOT NULL,
  task_json TEXT NOT NULL,                   -- the received tesTask (without read-only fields)
  version INTEGER NOT NULL DEFAULT 0         -- for conditional updates
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_nonce ON tasks (owner_iss, owner_sub, submit_nonce)
  WHERE submit_nonce IS NOT NULL;
CREATE TABLE IF NOT EXISTS executions (       -- intent saved before an external operation (tes.md section 4)
  task_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  executor_index INTEGER NOT NULL,
  phase TEXT NOT NULL,                       -- starting | started | finished
  pid INTEGER,
  exit_file TEXT,
  updated_at INTEGER NOT NULL,
  exit_code INTEGER,                         -- result once finished (decides the state after a restart)
  error TEXT,                                -- could not start, timeout or canceled
  PRIMARY KEY (task_id, attempt, executor_index)
);
CREATE TABLE IF NOT EXISTS output_transfers (
  task_id TEXT NOT NULL,
  path TEXT NOT NULL,
  url TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (task_id, path, url)           -- one row per destination, also from the same source
);
CREATE TABLE IF NOT EXISTS task_logs (
  task_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  start_time INTEGER,
  end_time INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  system_logs_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (task_id, attempt)
);
CREATE TABLE IF NOT EXISTS executor_logs (
  task_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  exit_code INTEGER,
  start_time INTEGER,
  end_time INTEGER,
  stdout_tail TEXT,
  stderr_tail TEXT,
  PRIMARY KEY (task_id, attempt, idx)
);
CREATE TABLE IF NOT EXISTS output_files (    -- record of outputs; also checks the owner of file:// inputs (C-25)
  task_id TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  owner_iss TEXT NOT NULL,
  owner_sub TEXT NOT NULL,
  actor TEXT,
  run_id TEXT
);
