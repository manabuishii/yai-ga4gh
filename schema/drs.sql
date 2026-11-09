-- Tables of yai-drs (design/drs.md section 5), kept language-independent
CREATE TABLE IF NOT EXISTS objects (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  name TEXT,
  mime_type TEXT,
  created_time INTEGER NOT NULL,        -- creation time of the contents (UTC seconds, DRS-OBJ-04)
  created_time_source TEXT NOT NULL,    -- given | file_mtime(D-022)
  registered_at INTEGER NOT NULL,
  description TEXT,
  metadata_policy TEXT NOT NULL CHECK (metadata_policy IN ('public', 'bearer', 'dataset')),
  access_policy TEXT NOT NULL CHECK (access_policy IN ('public', 'bearer', 'dataset')),
  dataset TEXT,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checksums (
  object_id TEXT NOT NULL REFERENCES objects(id),
  type TEXT NOT NULL,                   -- IANA Hash Name String (DRS-OBJ-05)
  checksum TEXT NOT NULL,
  PRIMARY KEY (object_id, type)
);
