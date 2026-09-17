"""SQLite 資料層（v3 平台真源）。書籍/章節/使用者/追書/類別皆在此。
生成產物檔案（analyze json / audio / timing / cover）仍在 storage/books/{bid}。
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
import threading
import uuid
import datetime as _dt
from contextlib import contextmanager

from . import settings

_thread = threading.local()
WRITE_LOCK = threading.RLock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT,
  role          TEXT    NOT NULL DEFAULT 'reader',
  created_at    TEXT    NOT NULL,
  session_version INTEGER NOT NULL DEFAULT 0,
  email         TEXT,
  last_login_at TEXT,
  account_status TEXT NOT NULL DEFAULT 'active',
  email_verified_at TEXT,
  email_verified_source TEXT NOT NULL DEFAULT '',
  pending_email TEXT,
  email_conflict INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS external_identities (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  provider              TEXT NOT NULL,
  issuer                TEXT NOT NULL,
  subject               TEXT NOT NULL,
  email_snapshot        TEXT NOT NULL DEFAULT '',
  email_verified        INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  last_login_at         TEXT,
  UNIQUE(issuer, subject),
  UNIQUE(account_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_external_identities_account ON external_identities(account_id);

CREATE TABLE IF NOT EXISTS auth_tokens (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose               TEXT NOT NULL,
  token_digest          TEXT NOT NULL UNIQUE,
  target_email          TEXT NOT NULL DEFAULT '',
  created_at            TEXT NOT NULL,
  expires_at            TEXT NOT NULL,
  used_at               TEXT,
  requested_ip           TEXT NOT NULL DEFAULT '',
  CHECK (purpose IN ('password_reset', 'email_verification'))
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_lookup ON auth_tokens(purpose, account_id, expires_at);

CREATE TABLE IF NOT EXISTS oauth_transactions (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  state_digest          TEXT NOT NULL UNIQUE,
  nonce_digest          TEXT NOT NULL,
  code_verifier_cipher  TEXT NOT NULL,
  provider              TEXT NOT NULL,
  purpose               TEXT NOT NULL,
  account_id            INTEGER REFERENCES users(id) ON DELETE CASCADE,
  return_path           TEXT NOT NULL DEFAULT '/',
  policy_ok             INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL,
  expires_at            TEXT NOT NULL,
  used_at               TEXT,
  CHECK (purpose IN ('login', 'link'))
);
CREATE INDEX IF NOT EXISTS idx_oauth_transactions_expiry ON oauth_transactions(expires_at);

CREATE TABLE IF NOT EXISTS oauth_link_confirmations (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider              TEXT NOT NULL,
  issuer                TEXT NOT NULL,
  subject               TEXT NOT NULL,
  email_snapshot        TEXT NOT NULL DEFAULT '',
  email_verified        INTEGER NOT NULL DEFAULT 0,
  confirmation_digest   TEXT NOT NULL UNIQUE,
  created_at            TEXT NOT NULL,
  expires_at            TEXT NOT NULL,
  used_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_oauth_link_confirmations_expiry
  ON oauth_link_confirmations(account_id, expires_at);

CREATE TABLE IF NOT EXISTS public_profiles (
  account_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
  display_name  TEXT    NOT NULL DEFAULT '',
  bio           TEXT    NOT NULL DEFAULT '',
  avatar_path   TEXT    NOT NULL DEFAULT '',
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS author_profiles (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id     TEXT    NOT NULL UNIQUE,
  slug          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  display_name  TEXT    NOT NULL,
  bio           TEXT    NOT NULL DEFAULT '',
  avatar_path   TEXT    NOT NULL DEFAULT '',
  status        TEXT    NOT NULL DEFAULT 'active',
  owner_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_author_profiles_owner ON author_profiles(owner_id, status);

CREATE TABLE IF NOT EXISTS author_profile_slug_aliases (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL REFERENCES author_profiles(id) ON DELETE CASCADE,
  slug       TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_author_profile_slug_aliases_profile
  ON author_profile_slug_aliases(profile_id);

CREATE TABLE IF NOT EXISTS categories (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS books (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  bid          TEXT    NOT NULL UNIQUE,
  owner_id     INTEGER NOT NULL REFERENCES users(id),
  author_profile_id INTEGER REFERENCES author_profiles(id) ON DELETE SET NULL,
  legacy_author_name TEXT NOT NULL DEFAULT '',
  category_id  INTEGER REFERENCES categories(id),
  title        TEXT    NOT NULL,
  synopsis     TEXT    NOT NULL DEFAULT '',
  tags         TEXT    NOT NULL DEFAULT '',
  category     TEXT    NOT NULL DEFAULT 'vocab',
  vocab_level  TEXT    NOT NULL DEFAULT 'AUTO',
  categories   TEXT    NOT NULL DEFAULT '[]',
  voices       TEXT    NOT NULL DEFAULT '{}',
  voice_prefs  TEXT    NOT NULL DEFAULT '{}',
  speaker_info TEXT    NOT NULL DEFAULT '{}',
  speaker_chapters TEXT NOT NULL DEFAULT '{}',
  settings     TEXT    NOT NULL DEFAULT '{}',
  cover_path   TEXT    NOT NULL DEFAULT '',
  serial       TEXT    NOT NULL DEFAULT '連載',
  status       TEXT    NOT NULL DEFAULT 'draft',
  reject_reason TEXT   NOT NULL DEFAULT '',
  chars        INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT    NOT NULL,
   updated_at   TEXT    NOT NULL,
   published_at TEXT,
   slug         TEXT,
   age_rating   TEXT NOT NULL DEFAULT 'general',
   content_warning TEXT NOT NULL DEFAULT '',
   visibility   TEXT NOT NULL DEFAULT 'public',
   access_policy TEXT NOT NULL DEFAULT 'free',
   last_chapter_at TEXT,
   audio_mode TEXT NOT NULL DEFAULT 'single',
   default_voice_id TEXT,
   audio_settings_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chapters (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter_key  TEXT,
  book_id      INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  title        TEXT    NOT NULL DEFAULT '',
  text         TEXT    NOT NULL,
  chars        INTEGER NOT NULL DEFAULT 0,
  status       TEXT    NOT NULL DEFAULT 'pending',
  audio        TEXT    NOT NULL DEFAULT 'none',
  error        TEXT    NOT NULL DEFAULT '',
  text_hash    TEXT    NOT NULL DEFAULT '',
  analyze_path TEXT    NOT NULL DEFAULT '',
  audio_path   TEXT    NOT NULL DEFAULT '',
   timing_path  TEXT    NOT NULL DEFAULT '',
   generated_at TEXT,
   analysed_hash TEXT NOT NULL DEFAULT '',
   publish_status TEXT NOT NULL DEFAULT 'published',
   published_at TEXT,
   audio_mode_override TEXT,
   voice_override_id TEXT,
   current_analysis_id INTEGER,
   active_audio_generation_id INTEGER,
   created_at TEXT NOT NULL DEFAULT '',
   updated_at TEXT NOT NULL DEFAULT '',
   UNIQUE (book_id, seq)
);

CREATE TABLE IF NOT EXISTS follows (
  user_id    INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
  book_id    INTEGER NOT NULL REFERENCES books(id)  ON DELETE CASCADE,
  created_at TEXT    NOT NULL,
  PRIMARY KEY (user_id, book_id)
);

CREATE INDEX IF NOT EXISTS idx_books_public ON books(status, published_at);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, seq);

CREATE TABLE IF NOT EXISTS chapter_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL DEFAULT '',
  saved_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (book_id, seq, text_hash)
);
CREATE INDEX IF NOT EXISTS idx_chapter_revisions_book ON chapter_revisions(book_id, seq, id);

CREATE TABLE IF NOT EXISTS banners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  subtitle TEXT NOT NULL DEFAULT '',
  image_desktop TEXT NOT NULL DEFAULT '',
  image_mobile TEXT NOT NULL DEFAULT '',
  link_type TEXT NOT NULL DEFAULT 'book',
  link_value TEXT NOT NULL DEFAULT '',
  alt_text TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  start_at TEXT,
  end_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  impression_count INTEGER NOT NULL DEFAULT 0,
  click_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_items (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'favorite',
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, book_id, kind)
);

CREATE TABLE IF NOT EXISTS reading_progress (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_seq INTEGER NOT NULL,
  position REAL NOT NULL DEFAULT 0,
  percent REAL NOT NULL DEFAULT 0,
  audio_position_seconds REAL NOT NULL DEFAULT 0,
  last_mode TEXT NOT NULL DEFAULT 'read',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS reading_history (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  last_chapter_seq INTEGER NOT NULL DEFAULT 0,
  last_read_at TEXT NOT NULL,
  PRIMARY KEY (user_id, book_id)
);

CREATE TABLE IF NOT EXISTS bookmarks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_seq INTEGER NOT NULL,
  position REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '',
  read_at TEXT,
  created_at TEXT NOT NULL,
  recipient_account_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  event_type TEXT,
  category TEXT,
  title_snapshot TEXT,
  body_snapshot TEXT,
  target_type TEXT,
  target_id TEXT,
  target_route TEXT,
  source_type TEXT,
  source_id TEXT,
  source_request_id INTEGER,
  source_generation_operation_id INTEGER,
  source_book_id INTEGER,
  dedupe_key TEXT
);

CREATE TABLE IF NOT EXISTS book_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_seq INTEGER,
  user_id INTEGER,
  session_key TEXT,
  event_type TEXT NOT NULL,
  duration INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_metrics_daily (
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  metric_date TEXT NOT NULL,
  unique_readers INTEGER NOT NULL DEFAULT 0,
  valid_reads INTEGER NOT NULL DEFAULT 0,
  completions INTEGER NOT NULL DEFAULT 0,
  follows INTEGER NOT NULL DEFAULT 0,
  favorites INTEGER NOT NULL DEFAULT 0,
  audio_plays INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (book_id, metric_date)
);

CREATE TABLE IF NOT EXISTS ranking_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ranking_type TEXT NOT NULL,
  ranking_window TEXT NOT NULL,
  category_id INTEGER,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  rank INTEGER NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  delta INTEGER NOT NULL DEFAULT 0,
  generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_seq INTEGER,
  body TEXT NOT NULL,
  spoiler INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'published',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS author_applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pen_name TEXT NOT NULL,
  bio TEXT NOT NULL DEFAULT '',
  rights_confirmed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  reject_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER,
  chapter_id INTEGER,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  progress INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  requested_by INTEGER,
  parent_job_id INTEGER,
  analysis_id INTEGER,
  audio_generation_id INTEGER,
  ai_provider_id INTEGER,
  ai_model TEXT,
  ai_config_version INTEGER,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  worker_instance_id TEXT,
  worker_version TEXT,
  worker_build_sha TEXT,
  claimed_at TEXT,
  worker_claim_token TEXT,
  usage_metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS worker_instances (
  instance_id TEXT PRIMARY KEY,
  worker_version TEXT NOT NULL,
  build_sha TEXT NOT NULL DEFAULT '',
  service_types TEXT NOT NULL DEFAULT '',
  process_id INTEGER,
  db_path TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  worker_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_worker_instances_active ON worker_instances(active, heartbeat_at);

CREATE TRIGGER IF NOT EXISTS generation_jobs_claim_identity_guard
BEFORE UPDATE OF status ON generation_jobs
WHEN OLD.status = 'pending' AND NEW.status = 'running'
 AND (COALESCE(NEW.worker_instance_id, '') = ''
      OR COALESCE(NEW.worker_version, '') = ''
      OR COALESCE(NEW.worker_build_sha, '') = ''
      OR COALESCE(NEW.worker_claim_token, '') = '')
BEGIN
  SELECT RAISE(ABORT, 'worker_identity_required');
END;

CREATE TABLE IF NOT EXISTS tts_providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  provider_type TEXT NOT NULL DEFAULT 'generic_http',
  base_url TEXT NOT NULL,
  synth_path TEXT NOT NULL DEFAULT '/synthesize',
  voices_path TEXT NOT NULL DEFAULT '/voices',
  auth_scheme TEXT NOT NULL DEFAULT 'bearer',
  secret_ciphertext TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0,
  last_status TEXT NOT NULL DEFAULT 'unknown',
  last_error TEXT NOT NULL DEFAULT '',
  last_checked_at TEXT,
  adapter_key TEXT NOT NULL DEFAULT 'generic_http',
  capabilities_path TEXT NOT NULL DEFAULT '/capabilities',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  capabilities_status TEXT NOT NULL DEFAULT 'unknown',
  capabilities_checked_at TEXT,
  capabilities_hash TEXT,
  capability_snapshot_version INTEGER NOT NULL DEFAULT 1,
  capabilities_declared_json TEXT NOT NULL DEFAULT '{}',
  capabilities_probed_json TEXT NOT NULL DEFAULT '{}',
  timeout_seconds REAL NOT NULL DEFAULT 95,
  config_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tts_provider_voices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id INTEGER NOT NULL REFERENCES tts_providers(id) ON DELETE CASCADE,
  voice_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT '',
  gender TEXT NOT NULL DEFAULT '',
  age TEXT,
  region TEXT NOT NULL DEFAULT '',
  languages_json TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  catalog_status TEXT NOT NULL DEFAULT 'available',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  UNIQUE(provider_id, voice_id)
);

CREATE TABLE IF NOT EXISTS ai_providers (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT    NOT NULL,
  provider_type      TEXT    NOT NULL,
  base_url           TEXT    NOT NULL,
  model              TEXT    NOT NULL,
  fallback_model     TEXT,
  secret_ciphertext  TEXT    NOT NULL DEFAULT '',
  enabled            INTEGER NOT NULL DEFAULT 1,
  is_default         INTEGER NOT NULL DEFAULT 0,
  config_version     INTEGER NOT NULL DEFAULT 1,
  last_status        TEXT    NOT NULL DEFAULT 'unknown',
  last_error         TEXT    NOT NULL DEFAULT '',
  last_checked_at    TEXT,
  last_used_at       TEXT,
  structured_capability_json TEXT NOT NULL DEFAULT '{}',
  structured_capability_status TEXT NOT NULL DEFAULT 'unknown',
  structured_capability_checked_at TEXT,
  structured_capability_expires_at TEXT,
  structured_capability_probe_version TEXT NOT NULL DEFAULT '',
  structured_capability_error TEXT NOT NULL DEFAULT '',
  created_by         INTEGER,
  created_at         TEXT    NOT NULL,
  updated_at         TEXT    NOT NULL,
  deleted_at         TEXT,
  CHECK (provider_type IN ('openai', 'openai_compatible', 'deepseek'))
);

CREATE TABLE IF NOT EXISTS chapter_analyses (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id                INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_id             INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  batch_job_id           INTEGER REFERENCES generation_jobs(id) ON DELETE SET NULL,
  source_text_hash       TEXT    NOT NULL,
  analysis_type          TEXT    NOT NULL DEFAULT 'speaker',
  schema_version         INTEGER NOT NULL,
  analysis_profile       TEXT    NOT NULL,
  status                 TEXT    NOT NULL,
  artifact_path          TEXT    NOT NULL DEFAULT '',
  ai_provider_id         INTEGER REFERENCES ai_providers(id) ON DELETE SET NULL,
  ai_model               TEXT,
  ai_config_version      INTEGER,
  prompt_version         TEXT    NOT NULL,
  emotion_policy_version TEXT,
  created_by             INTEGER,
  created_at             TEXT    NOT NULL,
  finished_at            TEXT,
  error                  TEXT    NOT NULL DEFAULT '',
  progress_json          TEXT    NOT NULL DEFAULT '{}',
  usage_metrics_json     TEXT    NOT NULL DEFAULT '{}',
  CHECK (analysis_type IN ('speaker')),
  CHECK (status IN ('queued', 'running', 'ready', 'failed'))
);

CREATE TABLE IF NOT EXISTS analysis_partials (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  cache_key             TEXT    NOT NULL UNIQUE,
  analysis_id           INTEGER REFERENCES chapter_analyses(id) ON DELETE SET NULL,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_id            INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  source_text_hash      TEXT    NOT NULL,
  chunk_index           INTEGER NOT NULL,
  chunk_input_hash      TEXT    NOT NULL,
  chunking_version      TEXT    NOT NULL,
  context_hash          TEXT    NOT NULL,
  prompt_hash           TEXT    NOT NULL,
  prompt_version        TEXT    NOT NULL,
  schema_version        INTEGER NOT NULL,
  analysis_profile      TEXT    NOT NULL,
  provider_identity     TEXT    NOT NULL,
  model_identity        TEXT    NOT NULL,
  provider_config_version TEXT,
  fallback_models_json  TEXT    NOT NULL DEFAULT '[]',
  language              TEXT    NOT NULL DEFAULT '',
  category              TEXT    NOT NULL DEFAULT '',
  state                 TEXT    NOT NULL DEFAULT 'pending',
  storage_path          TEXT    NOT NULL DEFAULT '',
  storage_sha256        TEXT    NOT NULL DEFAULT '',
  owner_token           TEXT,
  lease_expires_at      TEXT,
  request_count         INTEGER NOT NULL DEFAULT 0,
  retry_count           INTEGER NOT NULL DEFAULT 0,
  usage_metrics_json    TEXT    NOT NULL DEFAULT '{}',
  failure_code          TEXT    NOT NULL DEFAULT '',
  created_at            TEXT    NOT NULL,
  updated_at            TEXT    NOT NULL,
  last_used_at          TEXT,
  CHECK (state IN ('pending', 'running', 'succeeded', 'failed', 'stale')),
  UNIQUE (chapter_id, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_analysis_partials_analysis ON analysis_partials(analysis_id);
CREATE INDEX IF NOT EXISTS idx_analysis_partials_state_time ON analysis_partials(state, updated_at);
CREATE INDEX IF NOT EXISTS idx_analysis_partials_chapter ON analysis_partials(chapter_id, source_text_hash);

CREATE TABLE IF NOT EXISTS audio_generations (
  id                               INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id                          INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_id                       INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  mode                             TEXT    NOT NULL,
  source_text_hash                 TEXT    NOT NULL,
  analysis_id                      INTEGER REFERENCES chapter_analyses(id) ON DELETE SET NULL,
  character_registry_revision     INTEGER,
  voice_snapshot_json              TEXT    NOT NULL DEFAULT '{}',
  tts_profile_snapshot_json        TEXT    NOT NULL DEFAULT '{}',
  generation_key                   TEXT    NOT NULL UNIQUE,
  status                           TEXT    NOT NULL,
  audio_path                       TEXT    NOT NULL DEFAULT '',
  timing_path                      TEXT    NOT NULL DEFAULT '',
  tts_provider_id                  INTEGER REFERENCES tts_providers(id) ON DELETE SET NULL,
  provider_config_version          INTEGER,
  adapter_key                      TEXT,
  adapter_version                  TEXT,
  capabilities_hash                TEXT,
  emotion_policy                   TEXT    NOT NULL DEFAULT 'best_effort',
  emotion_schema_version           INTEGER,
  emotion_application_summary_json TEXT    NOT NULL DEFAULT '{}',
  requested_by                     INTEGER,
  created_at                       TEXT    NOT NULL,
  finished_at                      TEXT,
  error                            TEXT    NOT NULL DEFAULT '',
  CHECK (mode IN ('single', 'multi')),
  CHECK (status IN ('queued', 'running', 'ready', 'failed', 'failed_capability'))
);

CREATE TABLE IF NOT EXISTS expressive_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL UNIQUE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  speaker_id TEXT NOT NULL,
  voice_id TEXT NOT NULL,
  emotion TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  settings_json TEXT NOT NULL DEFAULT '{}',
  availability_status TEXT NOT NULL DEFAULT 'available',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(book_id, speaker_id, emotion, version),
  CHECK (emotion IN ('neutral', 'happy', 'sad', 'angry', 'tense')),
  CHECK (status IN ('active', 'archived', 'pending'))
);
CREATE INDEX IF NOT EXISTS idx_expressive_profiles_speaker ON expressive_profiles(book_id, speaker_id, emotion, status);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}',
  event_class TEXT NOT NULL DEFAULT 'legacy',
  policy_version TEXT NOT NULL DEFAULT 'legacy-v1',
  correction_of_id INTEGER,
  created_at TEXT NOT NULL
);

-- Roles/review workflow is additive.  Existing books, jobs, analyses and
-- audio rows remain untouched; these tables are the canonical request truth.
CREATE TABLE IF NOT EXISTS content_requests (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
  request_type          TEXT NOT NULL,
  requester_account_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  status                TEXT NOT NULL DEFAULT 'SUBMITTED',
  submitted_revision    TEXT NOT NULL,
  reviewer_account_id   INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  submitted_at          TEXT NOT NULL,
  review_started_at     TEXT,
  reviewed_at           TEXT,
  decision_reason       TEXT NOT NULL DEFAULT '',
  request_payload_json  TEXT NOT NULL DEFAULT '{}',
  generation_authorized INTEGER NOT NULL DEFAULT 0,
  operation_job_id      INTEGER,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  CHECK (request_type IN ('publish', 'unpublish', 'audiobook')),
  CHECK (status IN ('SUBMITTED', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'CANCELLED', 'INVALIDATED'))
);
CREATE INDEX IF NOT EXISTS idx_content_requests_queue
  ON content_requests(status, request_type, submitted_at, id);
CREATE INDEX IF NOT EXISTS idx_content_requests_book
  ON content_requests(book_id, request_type, created_at, id);
CREATE INDEX IF NOT EXISTS idx_content_requests_requester
  ON content_requests(requester_account_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_content_requests_reviewer
  ON content_requests(reviewer_account_id, status, updated_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_requests_one_active
  ON content_requests(book_id, request_type)
  WHERE status IN ('SUBMITTED', 'IN_REVIEW');

CREATE TABLE IF NOT EXISTS content_request_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id      INTEGER NOT NULL REFERENCES content_requests(id) ON DELETE RESTRICT,
  event_type      TEXT NOT NULL,
  from_status     TEXT,
  to_status       TEXT,
  actor_account_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  reason          TEXT NOT NULL DEFAULT '',
  metadata_json   TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL,
  CHECK (event_type IN ('submitted', 'review_started', 'approved', 'rejected',
                        'cancelled', 'invalidated', 'privileged_override'))
);
CREATE INDEX IF NOT EXISTS idx_content_request_events_request
  ON content_request_events(request_id, id);
CREATE INDEX IF NOT EXISTS idx_content_request_events_actor
  ON content_request_events(actor_account_id, created_at, id);

-- Ownership transfer is a separate workflow aggregate.  It changes only
-- books.owner_id when a completed transition commits; attribution and all
-- reader/generation references remain untouched.
CREATE TABLE IF NOT EXISTS ownership_transfer_requests (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE RESTRICT,
  requester_account_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  current_owner_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  target_account_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  status                TEXT NOT NULL DEFAULT 'REQUESTED',
  transfer_mode         TEXT NOT NULL DEFAULT 'normal',
  source_revision       TEXT NOT NULL,
  reason                TEXT NOT NULL DEFAULT '',
  reviewer_account_id   INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  target_acted_at       TEXT,
  review_started_at     TEXT,
  reviewed_at           TEXT,
  expires_at            TEXT NOT NULL,
  previous_owner_id     INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  new_owner_id          INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  completed_at          TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  CHECK (status IN ('REQUESTED', 'TARGET_ACCEPTED', 'IN_REVIEW', 'REJECTED',
                    'CANCELLED', 'EXPIRED', 'INVALIDATED', 'COMPLETED')),
  CHECK (transfer_mode IN ('normal', 'emergency')),
  CHECK (target_account_id <> current_owner_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ownership_transfer_one_active
  ON ownership_transfer_requests(book_id)
  WHERE status IN ('REQUESTED', 'TARGET_ACCEPTED', 'IN_REVIEW');
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_queue
  ON ownership_transfer_requests(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_book
  ON ownership_transfer_requests(book_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_requester
  ON ownership_transfer_requests(requester_account_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_target
  ON ownership_transfer_requests(target_account_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_reviewer
  ON ownership_transfer_requests(reviewer_account_id, status, updated_at, id);

CREATE TABLE IF NOT EXISTS ownership_transfer_events (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id         INTEGER NOT NULL REFERENCES ownership_transfer_requests(id) ON DELETE RESTRICT,
  event_type         TEXT NOT NULL,
  from_status        TEXT,
  to_status          TEXT,
  actor_account_id   INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  previous_owner_id  INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  new_owner_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
  reason             TEXT NOT NULL DEFAULT '',
  metadata_json      TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL,
  CHECK (event_type IN ('submitted', 'target_accepted', 'target_rejected',
                        'review_started', 'approved', 'rejected', 'cancelled',
                        'expired', 'invalidated', 'emergency_transfer'))
);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_events_request
  ON ownership_transfer_events(request_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ownership_transfer_events_actor
  ON ownership_transfer_events(actor_account_id, created_at, id);

CREATE TABLE IF NOT EXISTS character_registry_meta (
  book_id        INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
  revision       INTEGER NOT NULL DEFAULT 0,
  policy_version TEXT NOT NULL DEFAULT 'entity-resolution-v1',
  state_hash     TEXT NOT NULL DEFAULT '',
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_registry (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id          INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  character_id     TEXT NOT NULL,
  canonical_name   TEXT NOT NULL,
  aliases_json     TEXT NOT NULL DEFAULT '[]',
  status           TEXT NOT NULL DEFAULT 'provisional',
  merged_into      TEXT,
  voice_status     TEXT NOT NULL DEFAULT 'ready',
  registry_revision INTEGER NOT NULL DEFAULT 0,
  created_by       INTEGER,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE(book_id, character_id),
  CHECK (status IN ('provisional', 'active', 'merged', 'split', 'archived'))
);

CREATE TABLE IF NOT EXISTS character_aliases (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  surface               TEXT NOT NULL,
  normalized_surface    TEXT NOT NULL,
  relation_type         TEXT NOT NULL DEFAULT 'alias',
  scope_type            TEXT NOT NULL DEFAULT 'book',
  scope_key             TEXT NOT NULL DEFAULT '',
  target_character_id   TEXT,
  candidates_json       TEXT NOT NULL DEFAULT '[]',
  status                TEXT NOT NULL DEFAULT 'proposed',
  confidence            REAL NOT NULL DEFAULT 0.0,
  evidence_json         TEXT NOT NULL DEFAULT '[]',
  policy_version        TEXT NOT NULL DEFAULT 'entity-resolution-v1',
  registry_revision     INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  CHECK (scope_type IN ('scene', 'chapter', 'book', 'unresolved')),
  CHECK (status IN ('proposed', 'accepted', 'rejected', 'ambiguous', 'unresolved')),
  CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS idx_character_alias_lookup
  ON character_aliases(book_id, normalized_surface, scope_type, scope_key);

CREATE TABLE IF NOT EXISTS character_resolution_events (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  event_id              TEXT NOT NULL UNIQUE,
  event_type            TEXT NOT NULL,
  source_character_id   TEXT,
  target_character_id   TEXT,
  payload_json          TEXT NOT NULL DEFAULT '{}',
  actor_id              INTEGER,
  expected_revision     INTEGER,
  resulting_revision    INTEGER NOT NULL,
  policy_version        TEXT NOT NULL DEFAULT 'entity-resolution-v1',
  before_state_hash     TEXT NOT NULL DEFAULT '',
  after_state_hash      TEXT NOT NULL DEFAULT '',
  created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_events_book
  ON character_resolution_events(book_id, id);

CREATE TABLE IF NOT EXISTS character_resolution_proposals (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  source_character_id   TEXT,
  target_character_id   TEXT,
  confidence            REAL NOT NULL DEFAULT 0.0,
  evidence_json         TEXT NOT NULL DEFAULT '[]',
  reason                TEXT NOT NULL DEFAULT '',
  status                TEXT NOT NULL DEFAULT 'pending',
  policy_version        TEXT NOT NULL DEFAULT 'entity-resolution-v1',
  registry_revision     INTEGER NOT NULL,
  created_by            INTEGER,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  CHECK (status IN ('pending', 'auto_accepted', 'accepted', 'rejected', 'unresolved', 'stale')),
  CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE TABLE IF NOT EXISTS character_segment_resolutions (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  analysis_id           INTEGER NOT NULL REFERENCES chapter_analyses(id) ON DELETE CASCADE,
  segment_id            TEXT NOT NULL,
  source_speaker_id     TEXT NOT NULL,
  resolved_speaker_id   TEXT,
  status                TEXT NOT NULL DEFAULT 'unresolved',
  candidates_json       TEXT NOT NULL DEFAULT '[]',
  evidence_json         TEXT NOT NULL DEFAULT '[]',
  confidence            REAL NOT NULL DEFAULT 0.0,
  registry_revision     INTEGER NOT NULL DEFAULT 0,
  UNIQUE(analysis_id, segment_id),
  CHECK (status IN ('accepted', 'pending', 'ambiguous', 'unresolved'))
);

CREATE TABLE IF NOT EXISTS character_voice_conflicts (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id               INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  character_id          TEXT NOT NULL,
  voices_json           TEXT NOT NULL DEFAULT '[]',
  status                TEXT NOT NULL DEFAULT 'open',
  selected_voice        TEXT,
  registry_revision     INTEGER NOT NULL,
  created_at            TEXT NOT NULL,
  resolved_at           TEXT,
  CHECK (status IN ('open', 'resolved'))
);

CREATE TABLE IF NOT EXISTS character_migration_snapshots (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id             INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  registry_json       TEXT NOT NULL DEFAULT '[]',
  aliases_json        TEXT NOT NULL DEFAULT '[]',
  segment_resolutions_json TEXT NOT NULL DEFAULT '[]',
  voices_json         TEXT NOT NULL DEFAULT '{}',
  speaker_info_json   TEXT NOT NULL DEFAULT '{}',
  speaker_chapters_json TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL,
  restored_at         TEXT
);

CREATE TABLE IF NOT EXISTS policy_consents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  policy_type TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  accepted_at TEXT NOT NULL,
  UNIQUE(user_id, policy_type, policy_version)
);

CREATE INDEX IF NOT EXISTS idx_events_book_date ON book_events(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_comments_book ON comments(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON generation_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_providers_default ON ai_providers(enabled, is_default, deleted_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_providers_single_default ON ai_providers(is_default) WHERE is_default=1 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_analyses_chapter ON chapter_analyses(book_id, chapter_id, status);
CREATE INDEX IF NOT EXISTS idx_analyses_hash ON chapter_analyses(chapter_id, source_text_hash, status);
CREATE INDEX IF NOT EXISTS idx_audio_gen_chapter ON audio_generations(book_id, chapter_id, status);
CREATE INDEX IF NOT EXISTS idx_audio_gen_key ON audio_generations(generation_key);
"""

# Keep the original seed stable; additional genres can be added through admin CMS.
CATEGORY_NAMES = ["愛情", "推理", "玄幻", "都市", "武俠", "科幻", "懸疑", "歷史", "輕小說", "其他"]


def ts() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    con = getattr(_thread, "con", None)
    if con is None:
        os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
        con = sqlite3.connect(settings.DB_PATH, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        _thread.con = con
    return con


def init_db():
    """建表 + seed 類別 + admin bootstrap（冪等）。"""
    con = _conn()
    with WRITE_LOCK:
        con.executescript(SCHEMA)
        con.commit()
    _migrate_users_password_nullable()
    _migrate_schema()
    _seed_categories()
    _mark_email_conflicts()
    purge_expired_auth_state()
    _bootstrap_admin()


def _migrate_users_password_nullable():
    """將舊 users.password_hash NOT NULL 安全轉成 nullable。"""
    con = _conn()
    columns = {row[1]: row for row in con.execute("PRAGMA table_info(users)").fetchall()}
    password_column = columns.get("password_hash")
    if not password_column or not password_column[3]:
        return
    # SQLite cannot drop a NOT NULL constraint in place.  Keep foreign-key SQL
    # references stable while rebuilding only the users table.
    with WRITE_LOCK:
        con.execute("PRAGMA legacy_alter_table=ON")
        con.execute("PRAGMA foreign_keys=OFF")
        try:
            con.execute("ALTER TABLE users RENAME TO users_auth_legacy")
            con.execute(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "username TEXT NOT NULL UNIQUE COLLATE NOCASE,"
                "password_hash TEXT,"
                "role TEXT NOT NULL DEFAULT 'reader',"
                "created_at TEXT NOT NULL,"
                "session_version INTEGER NOT NULL DEFAULT 0,"
                "email TEXT,"
                "last_login_at TEXT,"
                "account_status TEXT NOT NULL DEFAULT 'active',"
                "email_verified_at TEXT,"
                "email_verified_source TEXT NOT NULL DEFAULT '',"
                "pending_email TEXT,"
                "email_conflict INTEGER NOT NULL DEFAULT 0"
                ")"
            )
            old_names = {row[1] for row in con.execute("PRAGMA table_info(users_auth_legacy)").fetchall()}
            targets = [
                "id", "username", "password_hash", "role", "created_at", "session_version",
                "email", "last_login_at", "account_status", "email_verified_at",
                "email_verified_source", "pending_email", "email_conflict",
            ]
            defaults = {
                "session_version": "0", "email": "NULL", "last_login_at": "NULL",
                "account_status": "'active'", "email_verified_at": "NULL",
                "email_verified_source": "''", "pending_email": "NULL", "email_conflict": "0",
            }
            expressions = [name if name in old_names else defaults.get(name, "NULL") for name in targets]
            con.execute(
                f"INSERT INTO users ({', '.join(targets)}) SELECT {', '.join(expressions)} "
                "FROM users_auth_legacy"
            )
            con.execute("DROP TABLE users_auth_legacy")
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA legacy_alter_table=OFF")


def _ensure_audit_integrity(con):
    """建立 audit_logs 的 append-only database guard（可重跑）。"""
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_logs_no_update "
        "BEFORE UPDATE ON audit_logs "
        "BEGIN SELECT RAISE(ABORT, 'audit_logs_append_only'); END"
    )
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete "
        "BEFORE DELETE ON audit_logs "
        "BEGIN SELECT RAISE(ABORT, 'audit_logs_append_only'); END"
    )


def _migrate_schema():
    """以可重跑的 SQLite migration 補上舊 v3 資料庫缺少的欄位。"""
    con = _conn()
    migrations = {
        "categories": [("enabled", "INTEGER NOT NULL DEFAULT 1")],
        "users": [
            ("email", "TEXT"),
            ("last_login_at", "TEXT"),
            ("account_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("session_version", "INTEGER NOT NULL DEFAULT 0"),
            ("email_verified_at", "TEXT"),
            ("email_verified_source", "TEXT NOT NULL DEFAULT ''"),
            ("pending_email", "TEXT"),
            ("email_conflict", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "public_profiles": [
            ("bio", "TEXT NOT NULL DEFAULT ''"),
        ],
        "ai_providers": [
            ("structured_capability_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("structured_capability_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("structured_capability_checked_at", "TEXT"),
            ("structured_capability_expires_at", "TEXT"),
            ("structured_capability_probe_version", "TEXT NOT NULL DEFAULT ''"),
            ("structured_capability_error", "TEXT NOT NULL DEFAULT ''"),
            ("max_concurrency", "INTEGER NOT NULL DEFAULT 1"),
            ("health_state", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("cooldown_until", "TEXT"),
        ],
        "books": [
            ("author_profile_id", "INTEGER REFERENCES author_profiles(id) ON DELETE SET NULL"),
            ("legacy_author_name", "TEXT NOT NULL DEFAULT ''"),
            ("slug", "TEXT"),
            ("age_rating", "TEXT NOT NULL DEFAULT 'general'"),
            ("content_warning", "TEXT NOT NULL DEFAULT ''"),
            ("visibility", "TEXT NOT NULL DEFAULT 'public'"),
            ("access_policy", "TEXT NOT NULL DEFAULT 'free'"),
            ("last_chapter_at", "TEXT"),
            ("audio_mode", "TEXT NOT NULL DEFAULT 'single'"),
            ("default_voice_id", "TEXT"),
            ("audio_settings_version", "INTEGER NOT NULL DEFAULT 1"),
        ],
        "chapters": [
            ("publish_status", "TEXT NOT NULL DEFAULT 'published'"),
            ("published_at", "TEXT"),
            ("chapter_key", "TEXT"),
            ("audio_mode_override", "TEXT"),
            ("voice_override_id", "TEXT"),
            ("current_analysis_id", "INTEGER"),
            ("active_audio_generation_id", "INTEGER"),
            # Older persistent databases predate the chapter timestamp
            # columns that the canonical V4 generation finalizer updates.
            # Keep this additive so an existing chapter can be finalized
            # safely after a real provider run.
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "tts_providers": [
            ("is_default", "INTEGER NOT NULL DEFAULT 0"),
            ("adapter_key", "TEXT NOT NULL DEFAULT 'generic_http'"),
            ("capabilities_path", "TEXT NOT NULL DEFAULT '/capabilities'"),
            ("capabilities_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("capabilities_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("capabilities_checked_at", "TEXT"),
            ("capabilities_hash", "TEXT"),
            ("capability_snapshot_version", "INTEGER NOT NULL DEFAULT 1"),
            ("capabilities_declared_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("capabilities_probed_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("timeout_seconds", "REAL NOT NULL DEFAULT 95"),
            ("config_version", "INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrency", "INTEGER NOT NULL DEFAULT 1"),
            ("health_state", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("cooldown_until", "TEXT"),
        ],
        "tts_provider_voices": [
            ("catalog_status", "TEXT NOT NULL DEFAULT 'available'"),
            ("capabilities_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("languages_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("age", "TEXT"),
        ],
        "expressive_profiles": [
            ("availability_status", "TEXT NOT NULL DEFAULT 'available'"),
        ],
        "audit_logs": [
            ("event_class", "TEXT NOT NULL DEFAULT 'legacy'"),
            ("policy_version", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
            ("correction_of_id", "INTEGER"),
        ],
        "generation_jobs": [
            ("parent_job_id", "INTEGER"),
            ("analysis_id", "INTEGER"),
            ("audio_generation_id", "INTEGER"),
            ("ai_provider_id", "INTEGER"),
            ("ai_model", "TEXT"),
            ("ai_config_version", "INTEGER"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
            ("worker_instance_id", "TEXT"),
            ("worker_version", "TEXT"),
            ("worker_build_sha", "TEXT"),
            ("claimed_at", "TEXT"),
            ("worker_claim_token", "TEXT"),
            ("usage_metrics_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("operation_id", "INTEGER"),
            ("service_type", "TEXT"),
            ("provider_id", "INTEGER"),
            ("provider_label", "TEXT NOT NULL DEFAULT ''"),
            ("provider_model", "TEXT NOT NULL DEFAULT ''"),
            ("provider_config_version", "TEXT NOT NULL DEFAULT ''"),
            ("capability_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("configuration_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("source_revision", "TEXT NOT NULL DEFAULT ''"),
            ("source_text_hash", "TEXT NOT NULL DEFAULT ''"),
            ("voice_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("next_attempt_at", "TEXT"),
            ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ("lease_until", "TEXT"),
            ("heartbeat_at", "TEXT"),
            ("attempt_id", "INTEGER"),
            ("failure_category", "TEXT NOT NULL DEFAULT ''"),
            ("retryable", "INTEGER NOT NULL DEFAULT 0"),
            ("legacy_marker", "TEXT NOT NULL DEFAULT ''"),
        ],
        "worker_instances": [
            ("service_types", "TEXT NOT NULL DEFAULT ''"),
        ],
        "reading_progress": [
            ("audio_position_seconds", "REAL NOT NULL DEFAULT 0"),
            ("last_mode", "TEXT NOT NULL DEFAULT 'read'"),
        ],
        "audio_generations": [
            ("character_registry_revision", "INTEGER"),
        ],
        "character_registry": [
            ("voice_status", "TEXT NOT NULL DEFAULT 'ready'"),
        ],
        "chapter_analyses": [
            ("batch_job_id", "INTEGER"),
            ("progress_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("usage_metrics_json", "TEXT NOT NULL DEFAULT '{}'"),
        ],
        "analysis_partials": [
            ("usage_metrics_json", "TEXT NOT NULL DEFAULT '{}'"),
        ],
        "notifications": [
            ("recipient_account_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
            ("event_type", "TEXT"),
            ("category", "TEXT"),
            ("title_snapshot", "TEXT"),
            ("body_snapshot", "TEXT"),
            ("target_type", "TEXT"),
            ("target_id", "TEXT"),
            ("target_route", "TEXT"),
            ("source_type", "TEXT"),
            ("source_id", "TEXT"),
            ("source_request_id", "INTEGER"),
            ("source_generation_operation_id", "INTEGER"),
            ("source_book_id", "INTEGER"),
            ("dedupe_key", "TEXT"),
        ],
    }
    with WRITE_LOCK:
        for table, columns in migrations.items():
            existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, definition in columns:
                if name not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        # Generation orchestration is additive.  Existing jobs remain valid;
        # no attempt rows are backfilled for historical executions.
        con.execute(
            "CREATE TABLE IF NOT EXISTS generation_operations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "request_id INTEGER UNIQUE REFERENCES content_requests(id) ON DELETE SET NULL,"
            "book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,"
            "chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,"
            "operation_type TEXT NOT NULL DEFAULT 'audiobook',"
            "requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "source_revision TEXT NOT NULL DEFAULT '',"
            "source_text_hash TEXT NOT NULL DEFAULT '',"
            "status TEXT NOT NULL DEFAULT 'queued',"
            "error_category TEXT NOT NULL DEFAULT '',"
            "error TEXT NOT NULL DEFAULT '',"
            "metadata_json TEXT NOT NULL DEFAULT '{}',"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            "started_at TEXT, finished_at TEXT"
            ")")
        con.execute(
            "CREATE TABLE IF NOT EXISTS generation_job_attempts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "job_id INTEGER NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,"
            "attempt_number INTEGER NOT NULL,"
            "service_type TEXT NOT NULL,"
            "provider_id INTEGER, provider_label TEXT NOT NULL DEFAULT '',"
            "provider_model TEXT NOT NULL DEFAULT '', provider_config_version TEXT NOT NULL DEFAULT '',"
            "worker_instance_id TEXT NOT NULL, worker_claim_token TEXT NOT NULL,"
            "started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT NOT NULL DEFAULT 'running',"
            "failure_category TEXT NOT NULL DEFAULT '', retryable INTEGER NOT NULL DEFAULT 0,"
            "diagnostics TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            "UNIQUE(job_id, attempt_number)"
            ")")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_operations_request ON generation_operations(request_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_operations_book ON generation_operations(book_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_jobs_orchestration_queue ON generation_jobs(service_type, status, next_attempt_at, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_jobs_provider_running ON generation_jobs(provider_id, service_type, status, lease_until)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_jobs_operation ON generation_jobs(operation_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_attempts_job ON generation_job_attempts(job_id, attempt_number)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_generation_attempts_provider ON generation_job_attempts(provider_id, service_type, started_at)")
        # Ownership transfer is additive and deliberately has no backfill:
        # legacy books keep their owner/attribution and acquire history only
        # when a new transfer is explicitly requested.
        con.execute(
            "CREATE TABLE IF NOT EXISTS ownership_transfer_requests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE RESTRICT,"
            "requester_account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,"
            "current_owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,"
            "target_account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,"
            "status TEXT NOT NULL DEFAULT 'REQUESTED',"
            "transfer_mode TEXT NOT NULL DEFAULT 'normal',"
            "source_revision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',"
            "reviewer_account_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,"
            "target_acted_at TEXT, review_started_at TEXT, reviewed_at TEXT,"
            "expires_at TEXT NOT NULL, previous_owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,"
            "new_owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT, completed_at TEXT,"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            "CHECK (status IN ('REQUESTED','TARGET_ACCEPTED','IN_REVIEW','REJECTED','CANCELLED','EXPIRED','INVALIDATED','COMPLETED')),"
            "CHECK (transfer_mode IN ('normal','emergency')),"
            "CHECK (target_account_id <> current_owner_id)"
            ")")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ownership_transfer_one_active "
            "ON ownership_transfer_requests(book_id) "
            "WHERE status IN ('REQUESTED','TARGET_ACCEPTED','IN_REVIEW')")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_queue ON ownership_transfer_requests(status, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_book ON ownership_transfer_requests(book_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_requester ON ownership_transfer_requests(requester_account_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_target ON ownership_transfer_requests(target_account_id, status, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_reviewer ON ownership_transfer_requests(reviewer_account_id, status, updated_at, id)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS ownership_transfer_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "request_id INTEGER NOT NULL REFERENCES ownership_transfer_requests(id) ON DELETE RESTRICT,"
            "event_type TEXT NOT NULL, from_status TEXT, to_status TEXT,"
            "actor_account_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,"
            "previous_owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,"
            "new_owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,"
            "reason TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',"
            "created_at TEXT NOT NULL,"
            "CHECK (event_type IN ('submitted','target_accepted','target_rejected','review_started','approved','rejected','cancelled','expired','invalidated','emergency_transfer'))"
            ")")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_events_request ON ownership_transfer_events(request_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ownership_transfer_events_actor ON ownership_transfer_events(actor_account_id, created_at, id)")
        # Legacy rows are interpreted by job type until they are touched by a
        # new orchestration enqueue path.  This is deliberately not a data
        # rewrite: old IDs, payloads and terminal outputs remain unchanged.
        con.execute(
            "UPDATE generation_jobs SET service_type=CASE "
            "WHEN job_type LIKE 'speaker_analysis%' THEN 'AI' "
            "WHEN job_type LIKE 'audio_%' THEN 'TTS' ELSE service_type END "
            "WHERE COALESCE(service_type,'')=''")
        # V4：chapter_key 為 nullable UNIQUE（Phase 2 才生成值）。
        # 現有 row 先補上以 rowid 為底的 key，避免 UNIQUE 撞空值。
        con.execute(
            "UPDATE chapters SET chapter_key = 'ck-' || id WHERE chapter_key IS NULL OR chapter_key = ''")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chapters_chapter_key ON chapters(chapter_key)")
        con.execute("UPDATE books SET slug = bid WHERE slug IS NULL OR slug = ''")
        con.execute("UPDATE chapters SET published_at = COALESCE(published_at, (SELECT published_at FROM books WHERE books.id = chapters.book_id)) WHERE publish_status = 'published'")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_books_slug ON books(slug)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_books_author_profile ON books(author_profile_id)")
        # Admin Console bounded read models.  These are additive indexes only;
        # no business state is duplicated or rewritten by the migration.
        con.execute("CREATE INDEX IF NOT EXISTS idx_users_admin_status_role ON users(account_status, role, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_users_admin_activity ON users(last_login_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_books_admin_owner_status ON books(owner_id, status, updated_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_books_admin_category_updated ON books(category_id, updated_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_categories_admin_order ON categories(enabled, sort, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_banners_admin_order ON banners(enabled, sort_order, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_reports_admin_queue ON reports(status, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_author_applications_admin_queue ON author_applications(status, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_admin_queue ON audit_logs(created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action_queue ON audit_logs(action, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_queue ON audit_logs(actor_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_target_queue ON audit_logs(target_type, target_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_class_queue ON audit_logs(event_class, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_correction ON audit_logs(correction_of_id, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient_read ON notifications(recipient_account_id, read_at, created_at, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient_created ON notifications(recipient_account_id, created_at, id)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_recipient_dedupe ON notifications(recipient_account_id, dedupe_key)")
        # Platform collection pagination indexes.  Each index matches an
        # authenticated scope plus the deterministic recency tie-breaker;
        # they are additive and safe to create repeatedly on SQLite.
        con.execute("CREATE INDEX IF NOT EXISTS idx_library_items_user_kind_created ON library_items(user_id, kind, created_at DESC, book_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_follows_user_created ON follows(user_id, created_at DESC, book_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_reading_history_user_recent ON reading_history(user_id, last_read_at DESC, book_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_user_created ON bookmarks(user_id, created_at DESC, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_books_author_public_order ON books(author_profile_id, status, published_at DESC, created_at DESC, id)")
        # Platform announcements are a separate, additive aggregate.  No
        # rows are backfilled from banners or notifications.
        con.execute(
            "CREATE TABLE IF NOT EXISTS announcements ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "title TEXT NOT NULL, body_text TEXT NOT NULL,"
            "cta_label TEXT NOT NULL DEFAULT '', cta_target TEXT NOT NULL DEFAULT '',"
            "audience_mode TEXT NOT NULL DEFAULT 'everyone',"
            "display_mode TEXT NOT NULL DEFAULT 'once_per_version',"
            "priority INTEGER NOT NULL DEFAULT 0,"
            "start_at TEXT, end_at TEXT, enabled INTEGER NOT NULL DEFAULT 0,"
            "display_version INTEGER NOT NULL DEFAULT 1,"
            "config_version INTEGER NOT NULL DEFAULT 1,"
            "archived_at TEXT, archived_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
            "CHECK (audience_mode IN ('everyone','guest','all_authenticated','specific_roles')),"
            "CHECK (display_mode IN ('once_per_version','once_per_session')),"
            "CHECK (display_version >= 1), CHECK (config_version >= 1)"
            ")")
        con.execute(
            "CREATE TABLE IF NOT EXISTS announcement_audience_roles ("
            "announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,"
            "role TEXT NOT NULL, PRIMARY KEY (announcement_id, role)"
            ")")
        con.execute(
            "CREATE TABLE IF NOT EXISTS announcement_acknowledgements ("
            "announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,"
            "account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "announcement_version INTEGER NOT NULL, acknowledged_at TEXT NOT NULL,"
            "PRIMARY KEY (announcement_id, account_id, announcement_version)"
            ")")
        con.execute("CREATE INDEX IF NOT EXISTS idx_announcements_active ON announcements(enabled, archived_at, start_at, end_at, priority, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_announcement_roles_role ON announcement_audience_roles(role, announcement_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_announcement_ack_account ON announcement_acknowledgements(account_id, announcement_id, announcement_version)")
        # Email uniqueness is enforced only when the pre-migration inventory is clean.
        # Conflicting legacy rows remain readable and are reported by the identity migration.
        email_values = [normalize_email(row["email"] or "") for row in con.execute(
            "SELECT email FROM users WHERE trim(COALESCE(email, '')) <> ''").fetchall()]
        email_conflicts = len(email_values) != len(set(email_values))
        if not email_conflicts:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_nocase ON users(lower(trim(email))) WHERE trim(email) <> ''")
        # ai_providers 單一 default invariant（部分唯一索引）
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_providers_single_default ON ai_providers(is_default) WHERE is_default=1 AND deleted_at IS NULL")
        # generation_jobs 的 V4 索引需在新增欄位後建立（舊 DB 無新欄位）
        for name, col in (("idx_jobs_parent", "parent_job_id"),
                          ("idx_jobs_analysis", "analysis_id"),
                          ("idx_jobs_audio_gen", "audio_generation_id")):
            if col in {r[1] for r in con.execute("PRAGMA table_info(generation_jobs)").fetchall()}:
                con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON generation_jobs({col})")
        if "batch_job_id" in {r[1] for r in con.execute("PRAGMA table_info(chapter_analyses)").fetchall()}:
            con.execute("CREATE INDEX IF NOT EXISTS idx_analyses_batch_job ON chapter_analyses(batch_job_id, status)")
        con.execute("DROP TRIGGER IF EXISTS generation_jobs_claim_identity_guard")
        con.execute(
            "CREATE TRIGGER IF NOT EXISTS generation_jobs_claim_identity_guard "
            "BEFORE UPDATE OF status ON generation_jobs "
            "WHEN OLD.status = 'pending' AND NEW.status = 'running' "
            "AND (COALESCE(NEW.worker_instance_id, '') = '' "
            "OR COALESCE(NEW.worker_version, '') = '' "
            "OR COALESCE(NEW.worker_build_sha, '') = '' "
            "OR COALESCE(NEW.worker_claim_token, '') = '') "
            "BEGIN SELECT RAISE(ABORT, 'worker_identity_required'); END")
        _ensure_audit_integrity(con)
        con.commit()


def _mark_email_conflicts():
    """標記 legacy normalized email 衝突；不選 winner、不合併帳號。"""
    con = _conn()
    with WRITE_LOCK:
        con.execute("UPDATE users SET email_conflict=0")
        groups = {}
        for row in con.execute("SELECT id, email FROM users WHERE trim(COALESCE(email, '')) <> ''").fetchall():
            normalized = normalize_email(row["email"] or "")
            if normalized:
                groups.setdefault(normalized, []).append(row["id"])
        conflict_ids = [uid for ids in groups.values() if len(ids) > 1 for uid in ids]
        if conflict_ids:
            marks = ",".join("?" for _ in conflict_ids)
            con.execute(f"UPDATE users SET email_conflict=1 WHERE id IN ({marks})", tuple(conflict_ids))
        con.commit()


def reset_db():
    """測試用：清空所有資料表（保留資料庫檔）。"""
    clear_tables()


def clear_tables():
    with WRITE_LOCK:
        con = _conn()
        # This is a development/test reset helper, not a product deletion
        # path.  Temporarily remove the database append-only guard so fixture
        # isolation can clear its own database, then always restore it.
        con.execute("DROP TRIGGER IF EXISTS audit_logs_no_update")
        con.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete")
        try:
            con.execute("DELETE FROM follows")
            con.execute("DELETE FROM library_items")
            con.execute("DELETE FROM reading_progress")
            con.execute("DELETE FROM reading_history")
            con.execute("DELETE FROM bookmarks")
            con.execute("DELETE FROM notifications")
            con.execute("DELETE FROM book_events")
            con.execute("DELETE FROM book_metrics_daily")
            con.execute("DELETE FROM ranking_snapshots")
            con.execute("DELETE FROM comments")
            con.execute("DELETE FROM reports")
            con.execute("DELETE FROM author_applications")
            con.execute("DELETE FROM generation_jobs")
            con.execute("DELETE FROM announcement_acknowledgements")
            con.execute("DELETE FROM announcement_audience_roles")
            con.execute("DELETE FROM announcements")
            con.execute("DELETE FROM content_request_events")
            con.execute("DELETE FROM content_requests")
            con.execute("DELETE FROM tts_providers")
            con.execute("DELETE FROM oauth_transactions")
            con.execute("DELETE FROM oauth_link_confirmations")
            con.execute("DELETE FROM auth_tokens")
            con.execute("DELETE FROM external_identities")
            con.execute("DELETE FROM audit_logs")
            con.execute("DELETE FROM policy_consents")
            con.execute("DELETE FROM banners")
            con.execute("DELETE FROM chapter_revisions")
            con.execute("DELETE FROM chapter_analyses")
            con.execute("DELETE FROM analysis_partials")
            con.execute("DELETE FROM audio_generations")
            con.execute("DELETE FROM expressive_profiles")
            con.execute("DELETE FROM ai_providers")
            con.execute("DELETE FROM chapters")
            con.execute("DELETE FROM books")
            con.execute("DELETE FROM author_profiles")
            con.execute("DELETE FROM public_profiles")
            con.execute("DELETE FROM users")
            con.execute("DELETE FROM categories")
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            _ensure_audit_integrity(con)
            con.commit()


def close_all():
    """測試用：關閉並清掉執行緒快取連線。"""
    con = getattr(_thread, "con", None)
    if con is not None:
        con.close()
        _thread.con = None


@contextmanager
def atomic():
    """跨多個資料層 helper 的可回滾交易。"""
    with WRITE_LOCK:
        depth = getattr(_thread, "transaction_depth", 0)
        _thread.transaction_depth = depth + 1
        try:
            yield _conn()
            if depth == 0:
                _conn().commit()
        except Exception:
            if depth == 0:
                _conn().rollback()
            raise
        finally:
            _thread.transaction_depth = depth


def execute(sql: str, params: tuple = ()):
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(sql, params)
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return cur


def query(sql: str, params: tuple = ()) -> list[dict]:
    con = _conn()
    cur = con.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple = ()):
    rows = query(sql, params)
    return rows[0] if rows else None


def scalars(sql: str, params: tuple = ()) -> list:
    return [r[0] for r in query(sql, params)]


# ---------- 類別 ----------

def _seed_categories():
    with WRITE_LOCK:
        con = _conn()
        con.executemany("INSERT OR IGNORE INTO categories(name, sort) VALUES(?, ?)",
                        [(n, i) for i, n in enumerate(CATEGORY_NAMES)])
        con.commit()


def list_categories():
    return query("SELECT id, name, sort, enabled FROM categories WHERE enabled = 1 ORDER BY sort ASC, id ASC")


def get_category(name_or_id):
    rows = query("SELECT id, name, sort FROM categories WHERE name = ? OR id = ?",
                 (name_or_id, name_or_id))
    return rows[0] if rows else None


def add_category(name: str, sort: int = 0):
    cur = execute("INSERT INTO categories(name, sort) VALUES(?, ?)", (name, sort))
    return cur.lastrowid


def update_category(cid: int, name: str = None, sort: int = None, enabled: bool = None):
    if name is not None:
        execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
    if sort is not None:
        execute("UPDATE categories SET sort = ? WHERE id = ?", (sort, cid))
    if enabled is not None:
        execute("UPDATE categories SET enabled = ? WHERE id = ?", (1 if enabled else 0, cid))


def set_category_enabled(cid: int, enabled: bool):
    execute("UPDATE categories SET enabled = ? WHERE id = ?", (1 if enabled else 0, cid))


def category_book_count(cid: int) -> int:
    return query_one("SELECT COUNT(*) n FROM books WHERE category_id = ?", (cid,))["n"]


def delete_category(cid: int):
    execute("DELETE FROM categories WHERE id = ?", (cid,))


# ---------- 使用者 ----------

SUPPORTED_ROLES = ("reader", "author", "reviewer", "admin", "super_admin")
ACTIVE_ACCOUNT_STATUSES = ("active",)

def get_user_by_id(uid: int):
    return query_one("SELECT * FROM users WHERE id = ?", (uid,))


def users_by_ids(ids: list[int]) -> dict:
    """L1-4：批次取使用者名稱，回 {uid: username}。"""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = query(f"SELECT id, username FROM users WHERE id IN ({marks})", tuple(ids))
    return {r["id"]: r["username"] for r in rows}


def get_user_by_name(name: str):
    return query_one("SELECT * FROM users WHERE username = ?", (name,))


def get_user_by_bname(name: str):
    return query_one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (name,))


def normalize_email(email: str) -> str:
    return email.strip().casefold() if isinstance(email, str) else ""


def get_users_by_email(email: str) -> list[dict]:
    normalized = normalize_email(email)
    if not normalized:
        return []
    # SQLite lower() is ASCII-oriented; normalize in Python so reads follow
    # the same Unicode casefold rule as writes.
    return [row for row in query("SELECT * FROM users WHERE email IS NOT NULL AND trim(email) <> ''")
            if normalize_email(row.get("email") or "") == normalized]


def get_user_by_email(email: str):
    """只在 normalized email 唯一時回傳帳號；legacy 衝突一律 ambiguous。"""
    rows = get_users_by_email(email)
    return rows[0] if len(rows) == 1 else None


def email_in_use(email: str, *, exclude_user_id: int = None) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    return any(row["id"] != exclude_user_id for row in get_users_by_email(normalized))


def create_user(username: str, password_hash: str, role: str):
    if role not in SUPPORTED_ROLES:
        raise ValueError("不支援的帳號角色")
    cur = execute(
        "INSERT INTO users(username, password_hash, role, created_at) VALUES(?, ?, ?, ?)",
        (username, password_hash, role, ts()))
    return cur.lastrowid


def record_policy_consent(user_id: int, policy_type: str, policy_version: str):
    execute(
        "INSERT OR IGNORE INTO policy_consents(user_id, policy_type, policy_version, accepted_at) VALUES(?, ?, ?, ?)",
        (user_id, policy_type[:40], policy_version[:20], ts()),
    )


def update_user_role(uid: int, role: str):
    if role not in SUPPORTED_ROLES:
        raise ValueError("不支援的帳號角色")
    with WRITE_LOCK:
        con = _conn()
        target = con.execute("SELECT id, role, account_status FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise KeyError("找不到使用者")
        if target["role"] == "super_admin" and role != "super_admin" and active_super_admin_count(con) <= 1:
            raise ValueError("LAST_SUPER_ADMIN_PROTECTION")
        if role == "super_admin" and target["account_status"] != "active":
            raise ValueError("停用或刪除的帳號不可成為 Super Admin")
        con.execute("UPDATE users SET role = ?, session_version = session_version + 1 WHERE id = ?", (role, uid))
        if target["role"] != role and target["account_status"] == "active":
            from .services import notifications as notification_service
            role_labels = {"reader": "讀者", "author": "作者", "reviewer": "Reviewer", "admin": "管理員", "super_admin": "Super Admin"}
            version = con.execute("SELECT session_version FROM users WHERE id=?", (uid,)).fetchone()["session_version"]
            notification_service.create_notification(
                uid, "account.role_changed", "account", "帳號角色已更新",
                f"你的帳號角色已更新為「{role_labels.get(role, role)}」。",
                dedupe_key=f"account:{uid}:role:{version}", target_type="", source_type="user",
                source_id=uid, legacy_kind="role_changed", con=con)
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def active_super_admin_count(con=None) -> int:
    con = con or _conn()
    row = con.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role='super_admin' AND account_status='active'"
    ).fetchone()
    return int(row["n"] if row else 0)


def update_user_password(uid: int, password_hash: str):
    execute("UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?", (password_hash, uid))


def bump_session_version(uid: int):
    execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (uid,))


def update_user_email(uid: int, email: str):
    raise ValueError("電子郵件必須透過驗證流程更新")


def set_pending_email(uid: int, email: str):
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("電子郵件不可為空白")
    if len(normalized) > 200 or "@" not in normalized:
        raise ValueError("電子郵件格式不正確")
    with WRITE_LOCK:
        con = _conn()
        matches = [row for row in con.execute(
            "SELECT id, email FROM users WHERE email IS NOT NULL AND trim(email) <> ''").fetchall()
                   if row["id"] != uid and normalize_email(row["email"] or "") == normalized]
        if matches:
            raise ValueError("此電子郵件已被使用")
        con.execute("UPDATE users SET pending_email=? WHERE id=?", (normalized, uid))
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def mark_email_verified(uid: int, *, source: str = "email"):
    execute("UPDATE users SET email_verified_at=?, email_verified_source=? WHERE id=?",
            (ts(), (source or "email")[:40], uid))


def promote_verified_email(uid: int, email: str, *, source: str = "email"):
    """在同一交易內重查唯一性後，將已驗證 pending email 提升為 canonical email。"""
    normalized = normalize_email(email)
    if not normalized or len(normalized) > 200 or "@" not in normalized:
        raise ValueError("電子郵件格式不正確")
    with WRITE_LOCK:
        con = _conn()
        matches = [row for row in con.execute(
            "SELECT id, email FROM users WHERE email IS NOT NULL AND trim(email) <> ''").fetchall()
                   if normalize_email(row["email"] or "") == normalized and row["id"] != uid]
        if matches:
            raise ValueError("此電子郵件已被使用")
        now = ts()
        con.execute("UPDATE users SET email=?, pending_email=NULL, email_verified_at=?, email_verified_source=?, "
                    "email_conflict=0, session_version=session_version+1 WHERE id=?",
                    (normalized, now, (source or "email")[:40], uid))
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def clear_pending_email(uid: int):
    execute("UPDATE users SET pending_email=NULL WHERE id=?", (uid,))


def has_password_credential(uid: int) -> bool:
    return bool(query_one("SELECT 1 FROM users WHERE id=? AND password_hash IS NOT NULL AND password_hash <> ''", (uid,)))


def get_external_identity(*, issuer: str, subject: str):
    return query_one("SELECT * FROM external_identities WHERE issuer=? AND subject=?", (issuer, subject))


def list_external_identities(account_id: int):
    return query("SELECT * FROM external_identities WHERE account_id=? ORDER BY id ASC", (account_id,))


def create_external_identity(account_id: int, *, provider: str, issuer: str, subject: str,
                             email_snapshot: str = "", email_verified: bool = False):
    now = ts()
    cur = execute(
        "INSERT INTO external_identities(account_id, provider, issuer, subject, email_snapshot, email_verified, created_at, updated_at, last_login_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, provider[:40], issuer[:200], subject[:300], normalize_email(email_snapshot)[:200],
         1 if email_verified else 0, now, now, now))
    return query_one("SELECT * FROM external_identities WHERE id=?", (cur.lastrowid,))


def touch_external_identity(identity_id: int, *, email_snapshot: str | None = None,
                            email_verified: bool | None = None):
    fields = ["updated_at=?", "last_login_at=?"]
    values = [ts(), ts()]
    if email_snapshot is not None:
        fields.append("email_snapshot=?")
        values.append(normalize_email(email_snapshot)[:200])
    if email_verified is not None:
        fields.append("email_verified=?")
        values.append(1 if email_verified else 0)
    values.append(identity_id)
    execute(f"UPDATE external_identities SET {', '.join(fields)} WHERE id=?", tuple(values))


def delete_external_identity(identity_id: int):
    execute("DELETE FROM external_identities WHERE id=?", (identity_id,))


def create_auth_token(account_id: int, *, purpose: str, token_digest: str, target_email: str,
                      expires_at: str, requested_ip: str = ""):
    if purpose not in ("password_reset", "email_verification"):
        raise ValueError("不支援的 token 用途")
    cur = execute(
        "INSERT INTO auth_tokens(account_id, purpose, token_digest, target_email, created_at, expires_at, requested_ip) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (account_id, purpose, token_digest, normalize_email(target_email)[:200], ts(), expires_at,
         (requested_ip or "")[:80]))
    return query_one("SELECT * FROM auth_tokens WHERE id=?", (cur.lastrowid,))


def invalidate_auth_tokens(account_id: int, purpose: str):
    execute("UPDATE auth_tokens SET used_at=COALESCE(used_at, ?) WHERE account_id=? AND purpose=? AND used_at IS NULL",
            (ts(), account_id, purpose))


def consume_auth_token(*, purpose: str, token_digest: str):
    """一次性 consume；回傳 row 或 None，並在同一寫鎖內避免 race。"""
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        row = con.execute("SELECT * FROM auth_tokens WHERE purpose=? AND token_digest=? AND used_at IS NULL "
                          "AND expires_at >= ?", (purpose, token_digest, now)).fetchone()
        if not row:
            return None
        cur = con.execute("UPDATE auth_tokens SET used_at=? WHERE id=? AND used_at IS NULL", (now, row["id"]))
        if cur.rowcount != 1:
            return None
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return dict(row)


def create_oauth_transaction(*, state_digest: str, nonce_digest: str, code_verifier_cipher: str,
                             provider: str, purpose: str, account_id: int | None,
                             return_path: str, policy_ok: bool, expires_at: str):
    if purpose not in ("login", "link"):
        raise ValueError("不支援的 OAuth transaction 用途")
    cur = execute(
        "INSERT INTO oauth_transactions(state_digest, nonce_digest, code_verifier_cipher, provider, purpose, account_id, "
        "return_path, policy_ok, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (state_digest, nonce_digest, code_verifier_cipher, provider[:40], purpose, account_id,
         return_path[:200], 1 if policy_ok else 0, ts(), expires_at))
    return query_one("SELECT * FROM oauth_transactions WHERE id=?", (cur.lastrowid,))


def consume_oauth_transaction(*, state_digest: str):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        row = con.execute("SELECT * FROM oauth_transactions WHERE state_digest=? AND used_at IS NULL AND expires_at >= ?",
                          (state_digest, now)).fetchone()
        if not row:
            return None
        cur = con.execute("UPDATE oauth_transactions SET used_at=? WHERE id=? AND used_at IS NULL", (now, row["id"]))
        if cur.rowcount != 1:
            return None
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return dict(row)


def create_oauth_link_confirmation(*, account_id: int, provider: str, issuer: str, subject: str,
                                   email_snapshot: str, email_verified: bool,
                                   confirmation_digest: str, expires_at: str):
    cur = execute(
        "INSERT INTO oauth_link_confirmations(account_id, provider, issuer, subject, email_snapshot, email_verified, "
        "confirmation_digest, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, provider[:40], issuer[:200], subject[:300], normalize_email(email_snapshot)[:200],
         1 if email_verified else 0, confirmation_digest, ts(), expires_at))
    return query_one("SELECT * FROM oauth_link_confirmations WHERE id=?", (cur.lastrowid,))


def consume_oauth_link_confirmation(*, account_id: int, confirmation_digest: str):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        row = con.execute(
            "SELECT * FROM oauth_link_confirmations WHERE account_id=? AND confirmation_digest=? "
            "AND used_at IS NULL AND expires_at >= ?", (account_id, confirmation_digest, now)).fetchone()
        if not row:
            return None
        cur = con.execute("UPDATE oauth_link_confirmations SET used_at=? WHERE id=? AND used_at IS NULL",
                          (now, row["id"]))
        if cur.rowcount != 1:
            return None
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return dict(row)


def purge_expired_auth_state():
    now = ts()
    execute("DELETE FROM auth_tokens WHERE expires_at < ? OR (used_at IS NOT NULL AND used_at < ?)", (now, now))
    execute("DELETE FROM oauth_transactions WHERE expires_at < ? OR (used_at IS NOT NULL AND used_at < ?)", (now, now))
    execute("DELETE FROM oauth_link_confirmations WHERE expires_at < ? OR (used_at IS NOT NULL AND used_at < ?)", (now, now))


def update_account_profile(uid: int, *, display_name: str = None, bio: str = None,
                           email: str = None, email_provided: bool = False):
    """更新 profile；primary email 必須走 pending verification flow。"""
    with WRITE_LOCK:
        con = _conn()
        if email_provided:
            raise ValueError("電子郵件必須透過驗證流程更新")
        now = ts()
        con.execute(
            "INSERT OR IGNORE INTO public_profiles(account_id, display_name, bio, avatar_path, created_at, updated_at) "
            "VALUES(?, '未命名使用者', '', '', ?, ?)", (uid, now, now))
        updates, values = [], []
        if display_name is not None:
            updates.append("display_name=?")
            values.append(display_name)
        if bio is not None:
            updates.append("bio=?")
            values.append(bio)
        if updates:
            updates.append("updated_at=?")
            values.extend([now, uid])
            con.execute(f"UPDATE public_profiles SET {', '.join(updates)} WHERE account_id=?", tuple(values))
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def delete_user(uid: int):
    # Identity/Profile contract only permits soft deletion.  Keep this legacy
    # helper safe for callers outside the HTTP router as well.
    soft_delete_user(uid)


def list_users():
    return query("SELECT id, username, role, created_at FROM users ORDER BY id ASC")


def create_user(username: str, password_hash: str | None, role: str, email: str = "",
                *, email_verified_at: str | None = None, email_verified_source: str = ""):
    if role not in SUPPORTED_ROLES:
        raise ValueError("不支援的帳號角色")
    email = normalize_email(email)
    with WRITE_LOCK:
        con = _conn()
        if email:
            matches = [row for row in con.execute(
                "SELECT email FROM users WHERE email IS NOT NULL AND trim(email) <> ''").fetchall()
                       if normalize_email(row["email"] or "") == email]
            if matches:
                raise ValueError("此電子郵件已被使用")
        cur = con.execute(
            "INSERT INTO users(username, password_hash, role, created_at, email, email_verified_at, email_verified_source) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (username, password_hash, role, ts(), email[:200] or None, email_verified_at, email_verified_source[:40]))
        ensure_public_profile(cur.lastrowid, username)
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return cur.lastrowid


def update_user_account_status(uid: int, status: str):
    if status not in ("active", "disabled"):
        raise ValueError("帳號狀態僅能為 active/disabled")
    with WRITE_LOCK:
        con = _conn()
        target = con.execute("SELECT role, account_status FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise KeyError("找不到使用者")
        if target["role"] == "super_admin" and target["account_status"] == "active" \
                and status != "active" and active_super_admin_count(con) <= 1:
            raise ValueError("LAST_SUPER_ADMIN_PROTECTION")
        con.execute("UPDATE users SET account_status=?, session_version=session_version + 1 WHERE id=?", (status, uid))
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def soft_delete_user(uid: int):
    """以 tombstone 保留所有 ownership、作品與稽核 foreign keys。"""
    with WRITE_LOCK:
        con = _conn()
        target = con.execute("SELECT role, account_status FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise KeyError("找不到使用者")
        if target["role"] == "super_admin" and target["account_status"] == "active" \
                and active_super_admin_count(con) <= 1:
            raise ValueError("LAST_SUPER_ADMIN_PROTECTION")
        con.execute("UPDATE users SET account_status='deleted', session_version=session_version + 1 WHERE id=?", (uid,))
        con.execute("UPDATE author_profiles SET status='tombstone', updated_at=? WHERE owner_id=? AND status <> 'tombstone'",
                    (ts(), uid))
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()


def touch_user_login(uid: int):
    execute("UPDATE users SET last_login_at=? WHERE id=?", (ts(), uid))


def list_users_paged(*, q: str = "", role: str = "", status: str = "",
                     page: int = 1, page_size: int = 20):
    """管理員使用者清單：支援 username/email 搜尋、角色/狀態篩選、server-side 分頁。"""
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(username LIKE ? COLLATE NOCASE OR email LIKE ? COLLATE NOCASE)")
        params += [like, like]
    if role in SUPPORTED_ROLES:
        where.append("role = ?")
        params.append(role)
    if status in ("active", "disabled", "deleted"):
        where.append("account_status = ?")
        params.append(status)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = query_one(f"SELECT COUNT(*) n FROM users{cond}", tuple(params))["n"]
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page))
    rows = query(
        f"SELECT id, username, email, role, account_status, created_at, last_login_at FROM users{cond} "
        f"ORDER BY id ASC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per))
    return {"items": rows, "total": total, "page": pg, "page_size": per,
            "total_pages": (total + per - 1) // per if total else 0}


def admin_count() -> int:
    return query_one("SELECT COUNT(*) n FROM users WHERE role = 'admin'")["n"]


def bootstrap_super_admin(account_id: int, *, actor_id: int | None = None) -> dict:
    """明確指定既有 Account 的非公開 bootstrap；不猜測、不建立新帳號。"""
    try:
        account_id = int(account_id)
    except (TypeError, ValueError) as error:
        raise ValueError("bootstrap 必須指定 canonical Account ID") from error
    with WRITE_LOCK:
        con = _conn()
        target = con.execute("SELECT * FROM users WHERE id=?", (account_id,)).fetchone()
        if not target:
            raise ValueError("找不到指定的 Account")
        if target["account_status"] != "active":
            raise ValueError("停用或刪除的 Account 不可 bootstrap")
        active = con.execute(
            "SELECT id FROM users WHERE role='super_admin' AND account_status='active' ORDER BY id"
        ).fetchall()
        if len(active) > 1:
            raise ValueError("存在多個 active Super Admin，bootstrap 已拒絕")
        if active:
            if active[0]["id"] != account_id:
                raise ValueError("已存在不同的 active Super Admin")
            result = dict(target)
            result["idempotent"] = True
            return result
        now = ts()
        con.execute("UPDATE users SET role='super_admin', session_version=session_version + 1 WHERE id=?", (account_id,))
        from .services import notifications as notification_service
        version = con.execute("SELECT session_version FROM users WHERE id=?", (account_id,)).fetchone()["session_version"]
        notification_service.create_notification(
            account_id, "account.role_changed", "account", "帳號角色已更新",
            "你的帳號已成為 Super Admin。", dedupe_key=f"account:{account_id}:role:{version}",
            source_type="user", source_id=account_id, legacy_kind="role_changed", con=con)
        add_audit_log(actor_id, "bootstrap_super_admin", "user", str(account_id),
                      {"bootstrap": True}, _con=con, _created_at=now)
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        result = dict(con.execute("SELECT * FROM users WHERE id=?", (account_id,)).fetchone())
        result["idempotent"] = False
        return result


def any_users() -> bool:
    return query_one("SELECT COUNT(*) n FROM users")["n"] > 0


def _bootstrap_admin():
    """users 表無任何帳號＋ADMIN_PASSWORD 有值 → 建立 admin。冪等。"""
    if not settings.ADMIN_PASSWORD or any_users():
        return
    from . import security
    now_user = query_one("SELECT COUNT(*) n FROM users")["n"]
    if now_user:
        return
    create_user(settings.ADMIN_USER, security.hash_password(settings.ADMIN_PASSWORD), "admin")
    import logging
    logging.getLogger("db").info("bootstrap admin 帳號：%s", settings.ADMIN_USER)


# ---------- Identity / profile ----------

def ensure_public_profile(account_id: int, fallback_name: str = "") -> dict:
    existing = get_public_profile(account_id)
    if existing:
        return existing
    now = ts()
    execute("INSERT OR IGNORE INTO public_profiles(account_id, display_name, bio, avatar_path, created_at, updated_at) VALUES(?, ?, '', '', ?, ?)",
            (account_id, (fallback_name or "未命名使用者")[:50], now, now))
    return get_public_profile(account_id)


def get_public_profile(account_id: int):
    return query_one("SELECT * FROM public_profiles WHERE account_id=?", (account_id,))


def update_public_profile(account_id: int, *, display_name: str = None, bio: str = None, avatar_path: str = None):
    ensure_public_profile(account_id)
    updates = []
    values = []
    if display_name is not None:
        updates.append("display_name=?")
        values.append(display_name)
    if bio is not None:
        updates.append("bio=?")
        values.append(bio)
    if avatar_path is not None:
        updates.append("avatar_path=?")
        values.append(avatar_path)
    if updates:
        updates.append("updated_at=?")
        values.extend([ts(), account_id])
        execute(f"UPDATE public_profiles SET {', '.join(updates)} WHERE account_id=?", tuple(values))
    return get_public_profile(account_id)


def get_author_profile(profile_id: int):
    return query_one("SELECT * FROM author_profiles WHERE id=?", (profile_id,))


def author_profiles_by_ids(ids: list[int]) -> dict:
    """Batch-load public author profiles for card read models."""
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = query(
        f"SELECT p.*, u.account_status AS owner_account_status "
        f"FROM author_profiles p LEFT JOIN users u ON u.id=p.owner_id WHERE p.id IN ({marks})",
        tuple(ids))
    return {row["id"]: row for row in rows}


def get_author_profile_by_public_id(public_id: str):
    return query_one("SELECT * FROM author_profiles WHERE public_id=?", (public_id,))


def get_author_profile_by_slug(slug: str):
    return query_one(
        "SELECT * FROM author_profiles WHERE slug=? COLLATE NOCASE "
        "UNION ALL SELECT p.* FROM author_profile_slug_aliases a "
        "JOIN author_profiles p ON p.id=a.profile_id WHERE a.slug=? COLLATE NOCASE LIMIT 1",
        (slug, slug))


def get_author_profile_by_identifier(identifier: str):
    """Resolve opaque public id first, then the human-readable slug/alias."""
    return get_author_profile_by_public_id(identifier) or get_author_profile_by_slug(identifier)


def list_author_profiles(owner_id: int = None, *, include_tombstone: bool = True):
    where, params = [], []
    if owner_id is not None:
        where.append("owner_id=?")
        params.append(owner_id)
    if not include_tombstone:
        where.append("status='active'")
    cond = " WHERE " + " AND ".join(where) if where else ""
    return query(f"SELECT * FROM author_profiles{cond} ORDER BY id ASC", tuple(params))


def create_author_profile(owner_id: int, *, public_id: str, slug: str, display_name: str,
                          bio: str = "", avatar_path: str = "", status: str = "active"):
    now = ts()
    cur = execute(
        "INSERT INTO author_profiles(public_id, slug, display_name, bio, avatar_path, status, owner_id, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (public_id, slug, display_name, bio, avatar_path, status, owner_id, now, now))
    execute("INSERT OR IGNORE INTO author_profile_slug_aliases(profile_id, slug, created_at) VALUES(?, ?, ?)",
            (cur.lastrowid, slug, now))
    return get_author_profile(cur.lastrowid)


def update_author_profile(profile_id: int, fields: dict):
    allowed = {"slug", "display_name", "bio", "avatar_path", "status"}
    values = [(key, value) for key, value in fields.items() if key in allowed]
    if not values:
        return get_author_profile(profile_id)
    assignments = ", ".join(f"{key}=?" for key, _ in values) + ", updated_at=?"
    with WRITE_LOCK:
        con = _conn()
        current = con.execute("SELECT slug FROM author_profiles WHERE id=?", (profile_id,)).fetchone()
        new_slug = next((value for key, value in values if key == "slug"), None)
        if current and new_slug and new_slug != current["slug"]:
            con.execute("INSERT OR IGNORE INTO author_profile_slug_aliases(profile_id, slug, created_at) VALUES(?, ?, ?)",
                        (profile_id, current["slug"], ts()))
        con.execute(f"UPDATE author_profiles SET {assignments} WHERE id=?",
                    tuple(value for _, value in values) + (ts(), profile_id))
        con.commit()
    return get_author_profile(profile_id)


def default_author_profile_id(owner_id: int):
    row = query_one("SELECT id FROM author_profiles WHERE owner_id=? AND status='active' ORDER BY id ASC LIMIT 1", (owner_id,))
    return row["id"] if row else None


def approve_author_application(application_id: int, reviewer_id: int, *, display_name: str,
                                bio: str, slug: str, public_id: str) -> dict:
    """原子完成作者申請、角色、persona、通知與 audit。"""
    with WRITE_LOCK:
        con = _conn()
        application = con.execute("SELECT * FROM author_applications WHERE id=?", (application_id,)).fetchone()
        if not application:
            raise KeyError("找不到作者申請")
        existing = con.execute(
            "SELECT * FROM author_profiles WHERE owner_id=? AND display_name=? AND bio=? ORDER BY id ASC LIMIT 1",
            (application["user_id"], display_name, bio)).fetchone()
        now = ts()
        if existing:
            profile_id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO author_profiles(public_id, slug, display_name, bio, avatar_path, status, owner_id, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, '', 'active', ?, ?, ?)",
                (public_id, slug, display_name, bio, application["user_id"], now, now))
            profile_id = cur.lastrowid
            con.execute("INSERT OR IGNORE INTO author_profile_slug_aliases(profile_id, slug, created_at) VALUES(?, ?, ?)",
                        (profile_id, slug, now))
        target_account = con.execute("SELECT role FROM users WHERE id=?", (application["user_id"],)).fetchone()
        from_role = target_account["role"] if target_account else "unknown"
        con.execute("UPDATE users SET role='author', session_version=session_version + 1 WHERE id=?", (application["user_id"],))
        con.execute("UPDATE author_applications SET status='approved', updated_at=? WHERE id=?", (now, application_id))
        add_audit_log(reviewer_id, "approve_author_application", "author_application", application_id,
                      {"authorProfileId": profile_id, "authorPublicId": public_id},
                      _con=con, _created_at=now)
        role_details = {"fromRole": from_role, "toRole": "author",
                        "source": "author_application"}
        add_audit_log(reviewer_id, "role_granted", "user", application["user_id"], role_details,
                      _con=con, _created_at=now)
        from .services import notifications as notification_service
        try:
            notification_service.create_notification(
                application["user_id"], "author_application.approved", "author", "作者申請已通過",
                "現在可以建立自己的作品。", dedupe_key=f"author_application:{application_id}:approved",
                target_type="shelf", target_route="#/mine", source_type="author_application",
                source_id=application_id, legacy_kind="author_approved", con=con)
        except Exception:
            con.rollback()
            raise
        con.commit()
    return get_author_profile(profile_id)


# ---------- 書籍 ----------

def create_book(owner_id: int, **fields) -> str:
    auto_author_profile = fields.pop("_auto_author_profile", True)
    cols = ["bid", "owner_id", "author_profile_id", "legacy_author_name", "title", "slug", "category", "vocab_level", "categories",
            "voices", "voice_prefs", "speaker_info", "speaker_chapters",
            "settings", "cover_path", "serial", "status", "reject_reason",
            "chars", "created_at", "updated_at", "published_at", "synopsis", "tags", "category_id"]
    data = {k: fields.get(k) for k in cols}
    data["owner_id"] = owner_id
    _defaults = {
        "bid": f"b{int(_dt.datetime.now().timestamp())}-{uuid.uuid4().hex[:8]}",
        "created_at": ts(), "updated_at": ts(),
        "categories": json.dumps([fields.get("category") or "vocab"]),
        "voices": "{}", "voice_prefs": "{}", "speaker_info": "{}",
        "speaker_chapters": "{}", "settings": "{}", "vocab_level": "AUTO",
        "serial": "連載", "status": "draft", "chars": 0, "synopsis": "",
        "tags": "", "category": "vocab", "cover_path": "", "reject_reason": "",
        "category_id": None,
         "author_profile_id": default_author_profile_id(owner_id) if auto_author_profile else None,
         # 新資料不得把 login username 當成公開作者名；legacy text 只能由
         # migration 或明確的 historical attribution 提供。
         "legacy_author_name": "",
    }
    _defaults["slug"] = fields.get("slug") or _defaults["bid"]
    for k, v in _defaults.items():
        if data.get(k) is None:
            data[k] = v
    data.setdefault("settings", "{}")
    data.setdefault("vocab_level", "AUTO")
    data.setdefault("serial", "連載")
    data.setdefault("status", "draft")
    data.setdefault("chars", 0)
    data.setdefault("synopsis", "")
    data.setdefault("tags", "")
    data.setdefault("category", "vocab")
    data.setdefault("cover_path", "")
    data.setdefault("reject_reason", "")
    data.setdefault("category_id", None)
    # dev mode：owner_id=0（虛擬 admin）無對應 users 列，用獨立連線關 FK 建立草稿
    dev_owner_row = query_one("SELECT COUNT(*) n FROM users WHERE id = 0")
    is_dev_owner = owner_id == 0 and not (dev_owner_row and dev_owner_row["n"])
    with WRITE_LOCK:
        sql = (f"INSERT INTO books({','.join(k for k in data if data[k] is not None)}) "
               f"VALUES({','.join('?' for _ in data if data[_] is not None)})")
        values = tuple(v for v in data.values() if v is not None)
        if is_dev_owner:
            con = sqlite3.connect(settings.DB_PATH, timeout=60, check_same_thread=False)
            try:
                con.execute("PRAGMA foreign_keys=OFF")
                con.execute(sql, values)
                con.commit()
            finally:
                con.close()
        else:
            con = _conn()
            con.execute(sql, values)
            con.commit()
    return data["bid"]


def get_book_row(bid: str):
    return query_one("SELECT * FROM books WHERE bid = ?", (bid,))


def get_book_by_rowid(bid_id: int):
    return query_one("SELECT * FROM books WHERE id = ?", (bid_id,))


def update_book(bid: str, fields: dict):
    if not fields:
        return
    fields["updated_at"] = ts()
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE books SET {sets} WHERE bid = ?", (*fields.values(), bid))


def bulk_update_book_category(book_ids: list[str], category_id: int) -> tuple[list[str], list[str]]:
    """以單一交易批次更新書籍主分類，回傳 (updated, missing)。"""
    ids = list(dict.fromkeys(book_ids))
    if not ids:
        return [], []
    marks = ",".join("?" for _ in ids)
    with WRITE_LOCK:
        con = _conn()
        rows = con.execute(f"SELECT bid FROM books WHERE bid IN ({marks})", tuple(ids)).fetchall()
        found = {row["bid"] for row in rows}
        missing = [bid for bid in ids if bid not in found]
        if missing:
            return [], missing
        now = ts()
        con.execute(f"UPDATE books SET category_id = ?, updated_at = ? WHERE bid IN ({marks})",
                    (category_id, now, *ids))
        con.commit()
    return ids, []


def set_book_status(bid: str, status: str, reject_reason: str = None):
    now = ts()
    fields = {"status": status, "updated_at": now}
    if reject_reason is not None:
        fields["reject_reason"] = reject_reason
    if status == "approved":
        r = get_book_row(bid)
        if r and not r.get("published_at"):
            fields["published_at"] = now
            fields["last_chapter_at"] = now
    update_book(bid, fields)


def delete_book_row(bid: str):
    execute("DELETE FROM books WHERE bid = ?", (bid,))


def book_published(bid: str) -> bool:
    r = get_book_row(bid)
    return bool(r and r["status"] == "approved" and r["published_at"])


def book_chapter_count(bid_id: int) -> int:
    return query_one("SELECT COUNT(*) n FROM chapters WHERE book_id = ?", (bid_id,))["n"]


def book_chars(bid_id: int) -> int:
    return query_one("SELECT COALESCE(SUM(chars),0) n FROM chapters WHERE book_id = ?", (bid_id,))["n"]


def book_audio_ready_count(bid_id: int) -> int:
    return query_one("SELECT COUNT(*) n FROM chapters WHERE book_id = ? AND audio='ready'", (bid_id,))["n"]


def book_category_name(bid_id: int):
    r = query_one("SELECT c.name name FROM categories c JOIN books b ON b.category_id=c.id WHERE b.id=?", (bid_id,))
    return r["name"] if r else None


def category_names_by_books(bid_ids: list[int]) -> dict:
    """L1-4：批次．取多本作品的分類名稱，回 {book_id: category_name}。"""
    bid_ids = [i for i in bid_ids if i]
    if not bid_ids:
        return {}
    marks = ",".join("?" * len(bid_ids))
    rows = query(
        f"SELECT b.id id, c.name name FROM categories c JOIN books b ON b.category_id=c.id "
        f"WHERE b.id IN ({marks})",
        tuple(bid_ids),
    )
    return {r["id"]: r["name"] for r in rows}


# ---------- 章節 ----------

def list_chapters(bid_id: int):
    return query("SELECT * FROM chapters WHERE book_id = ? ORDER BY seq ASC", (bid_id,))


def chapter_stats_all(bid_ids: list[int]) -> dict:
    """Batch chapter counts for bounded book-card projections."""
    bid_ids = [i for i in bid_ids if i]
    if not bid_ids:
        return {}
    marks = ",".join("?" * len(bid_ids))
    rows = query(
        f"SELECT book_id, COUNT(*) total, SUM(CASE WHEN audio='ready' THEN 1 ELSE 0 END) ready, "
        "SUM(CASE WHEN status='analyzed' THEN 1 ELSE 0 END) analyzed "
        f"FROM chapters WHERE book_id IN ({marks}) GROUP BY book_id",
        tuple(bid_ids),
    )
    return {r["book_id"]: (r["total"], r["ready"] or 0, r["analyzed"] or 0) for r in rows}


def get_chapter(bid_id: int, seq: int):
    return query_one("SELECT * FROM chapters WHERE book_id = ? AND seq = ?", (bid_id, seq))


def get_chapter_by_id(ch_id: int):
    return query_one("SELECT * FROM chapters WHERE id = ?", (ch_id,))


def insert_chapter(bid_id: int, seq: int, title: str, text: str) -> None:
    import hashlib
    import uuid
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
    key = f"ck-{uuid.uuid4().hex[:12]}"
    execute(
        "INSERT INTO chapters(book_id, chapter_key, seq, title, text, chars, text_hash, analysed_hash) VALUES(?, ?, ?, ?, ?, ?, ?, '')",
        (bid_id, key, seq, title, text, len(text), h))


def update_chapter(bid_id: int, seq: int, fields: dict):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE chapters SET {sets} WHERE book_id = ? AND seq = ?",
            (*fields.values(), bid_id, seq))


def delete_chapter_row(bid_id: int, seq: int):
    execute("DELETE FROM chapters WHERE book_id = ? AND seq = ?", (bid_id, seq))


# ---------- 章節版本歷史 ----------

def save_chapter_revision(book_row_id: int, seq: int, title: str, text: str, saved_by: int = None):
    import hashlib
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
    cur = execute(
        "INSERT OR IGNORE INTO chapter_revisions(book_id, seq, title, text, text_hash, saved_by, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (book_row_id, seq, title, text or "", h, saved_by, ts()))
    new_id = cur.lastrowid
    if not new_id:
        row = query_one("SELECT id FROM chapter_revisions WHERE book_id=? AND seq=? AND text_hash=?",
                        (book_row_id, seq, h))
        new_id = row["id"] if row else None
    # 每章最多保留最近 20 個版本，避免無限制膨脹
    stale = query("SELECT id FROM chapter_revisions WHERE book_id=? AND seq=? ORDER BY id DESC LIMIT -1 OFFSET 20", (book_row_id, seq))
    for row in stale:
        execute("DELETE FROM chapter_revisions WHERE id=?", (row["id"],))
    return new_id


def list_chapter_revisions(book_row_id: int, seq: int):
    return query("SELECT id, title, text_hash, saved_by, created_at, LENGTH(text) AS chars FROM chapter_revisions WHERE book_id=? AND seq=? ORDER BY id DESC LIMIT 50", (book_row_id, seq))


def get_chapter_revision(revision_id: int):
    return query_one("SELECT * FROM chapter_revisions WHERE id=?", (revision_id,))


def next_seq(bid_id: int) -> int:
    r = query_one("SELECT COALESCE(MAX(seq), -1) m FROM chapters WHERE book_id = ?", (bid_id,))
    return int(r["m"]) + 1


def reorder_chapters(bid_id: int, ordered_ids: list[int]) -> None:
    """以指定的章節 id 順序重排 seq=0..N-1，保留 row 身份（id / chapter_key）。

    只更新 seq 欄位，不刪除重建 row，也不改名任何產物檔。
    """
    rows = query("SELECT id FROM chapters WHERE book_id = ? ORDER BY seq ASC", (bid_id,))
    if len(rows) != len(ordered_ids) or {r["id"] for r in rows} != set(ordered_ids):
        raise ValueError("章節順序不正確")
    with WRITE_LOCK:
        con = _conn()
        # 兩階段更新避開 UNIQUE(book_id, seq)：先全部移到負向暫存，再寫入最終順序
        for i, r in enumerate(rows):
            con.execute("UPDATE chapters SET seq=? WHERE id=?", (-(i + 1), r["id"]))
        for new_seq, ch_id in enumerate(ordered_ids):
            con.execute("UPDATE chapters SET seq=? WHERE id=?", (new_seq, ch_id))
        con.commit()


def set_chapter_paths(bid_id: int):
    rows = query("SELECT * FROM chapters WHERE book_id = ?", (bid_id,))
    b = query_one("SELECT bid FROM books WHERE id = ?", (bid_id,))
    bid = b["bid"]
    with WRITE_LOCK:
        con = _conn()
        for r in rows:
            ana = f"storage/books/{bid}/chapters/{r['seq']:04d}.json"
            aud = f"storage/books/{bid}/audio/{r['seq']:04d}.mp3"
            tmg = f"storage/books/{bid}/audio/{r['seq']:04d}.timing.json"
            con.execute(
                "UPDATE chapters SET analyze_path=?, audio_path=?, timing_path=? WHERE id=?",
                (ana, aud, tmg, r["id"]))
        con.commit()


# ---------- 追書 ----------

def add_follow(user_id: int, bid_id: int):
    execute("INSERT OR IGNORE INTO follows(user_id, book_id, created_at) VALUES(?, ?, ?)",
            (user_id, bid_id, ts()))


def remove_follow(user_id: int, bid_id: int):
    execute("DELETE FROM follows WHERE user_id = ? AND book_id = ?", (user_id, bid_id))


def is_following(user_id: int, bid_id: int) -> bool:
    return bool(query_one("SELECT 1 FROM follows WHERE user_id = ? AND book_id = ?", (user_id, bid_id)))


# ---------- 首頁、書架、閱讀與營運 ----------

def list_banners(active_only: bool = True):
    where = "WHERE enabled = 1" if active_only else ""
    rows = query(f"SELECT * FROM banners {where} ORDER BY sort_order ASC, id ASC")
    if not active_only:
        return rows
    now = ts()
    return [r for r in rows if (not r["start_at"] or r["start_at"] <= now) and (not r["end_at"] or r["end_at"] >= now)]


def create_banner(fields: dict) -> int:
    now = ts()
    cur = execute(
        "INSERT INTO banners(title, subtitle, image_desktop, image_mobile, link_type, link_value, alt_text, sort_order, start_at, end_at, enabled, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fields.get("title", ""), fields.get("subtitle", ""), fields.get("imageDesktop", ""),
         fields.get("imageMobile", ""), fields.get("linkType", "book"), fields.get("linkValue", ""),
         fields.get("altText", ""), int(fields.get("sortOrder", 0)), fields.get("startAt"), fields.get("endAt"),
         1 if fields.get("enabled", True) else 0, now, now))
    return cur.lastrowid


def update_banner(banner_id: int, fields: dict):
    mapping = {"title": "title", "subtitle": "subtitle", "imageDesktop": "image_desktop", "imageMobile": "image_mobile",
               "linkType": "link_type", "linkValue": "link_value", "altText": "alt_text", "sortOrder": "sort_order",
               "startAt": "start_at", "endAt": "end_at", "enabled": "enabled"}
    updates = [(mapping[k], v) for k, v in fields.items() if k in mapping]
    if not updates:
        return
    values = [1 if col == "enabled" and v else 0 if col == "enabled" else v for col, v in updates]
    sets = ", ".join(f"{col} = ?" for col, _ in updates) + ", updated_at = ?"
    execute(f"UPDATE banners SET {sets} WHERE id = ?", (*values, ts(), banner_id))


def delete_banner(banner_id: int):
    execute("DELETE FROM banners WHERE id = ?", (banner_id,))


def add_library_item(user_id: int, book_id: int, kind: str):
    execute("INSERT OR IGNORE INTO library_items(user_id, book_id, kind, created_at) VALUES(?, ?, ?, ?)",
            (user_id, book_id, kind, ts()))


def remove_library_item(user_id: int, book_id: int, kind: str):
    execute("DELETE FROM library_items WHERE user_id = ? AND book_id = ? AND kind = ?", (user_id, book_id, kind))


def has_library_item(user_id: int, book_id: int, kind: str) -> bool:
    return bool(query_one("SELECT 1 FROM library_items WHERE user_id=? AND book_id=? AND kind=?", (user_id, book_id, kind)))


def list_library(user_id: int, kind: str = None):
    where = "li.user_id = ?"
    params = [user_id]
    if kind:
        where += " AND li.kind = ?"
        params.append(kind)
    return query(f"SELECT b.*, li.kind AS library_kind, li.created_at AS library_created_at FROM library_items li JOIN books b ON b.id=li.book_id WHERE {where} ORDER BY li.created_at DESC", tuple(params))


def list_library_page(user_id: int, *, kind: str = None, page: int, page_size: int, public_only: bool = True):
    """Return one principal-scoped library page and its exact matching count."""
    followed = kind == "followed"
    source = "follows" if followed else "library_items"
    kind_expr = "'followed'" if followed else "li.kind"
    where = ["li.user_id = ?"]
    params = [user_id]
    if kind and not followed:
        where.append("li.kind = ?")
        params.append(kind)
    if public_only:
        where.append("b.status='approved' AND b.published_at IS NOT NULL")
    predicate = " AND ".join(where)
    total = query_one(f"SELECT COUNT(*) AS total FROM {source} li JOIN books b ON b.id=li.book_id WHERE {predicate}", tuple(params))["total"]
    rows = query(
        f"SELECT b.*, {kind_expr} AS library_kind, li.created_at AS library_created_at "
        f"FROM {source} li JOIN books b ON b.id=li.book_id WHERE {predicate} "
        "ORDER BY li.created_at DESC, li.book_id DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size))
    return rows, total


def save_progress(user_id: int, book_id: int, chapter_seq: int, position: float, percent: float,
                  audio_position_seconds: float = 0.0, last_mode: str = "read"):
    now = ts()
    execute(
        "INSERT INTO reading_progress(user_id, book_id, chapter_seq, position, percent, audio_position_seconds, last_mode, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, book_id) DO UPDATE SET chapter_seq=excluded.chapter_seq, position=excluded.position, "
        "percent=excluded.percent, audio_position_seconds=excluded.audio_position_seconds, "
        "last_mode=excluded.last_mode, updated_at=excluded.updated_at",
        (user_id, book_id, chapter_seq, position, percent, float(audio_position_seconds), str(last_mode)[:10], now))
    execute("INSERT INTO reading_history(user_id, book_id, last_chapter_seq, last_read_at) VALUES(?, ?, ?, ?) ON CONFLICT(user_id, book_id) DO UPDATE SET last_chapter_seq=excluded.last_chapter_seq, last_read_at=excluded.last_read_at",
            (user_id, book_id, chapter_seq, now))


def get_progress(user_id: int, book_id: int = None):
    if book_id is None:
        return query("SELECT * FROM reading_progress WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
    return query_one("SELECT * FROM reading_progress WHERE user_id=? AND book_id=?", (user_id, book_id))


def list_history(user_id: int, limit: int = 20):
    return query("SELECT b.*, h.last_chapter_seq, h.last_read_at FROM reading_history h JOIN books b ON b.id=h.book_id WHERE h.user_id=? ORDER BY h.last_read_at DESC LIMIT ?", (user_id, max(1, min(limit, 100))))


def list_history_page(user_id: int, *, page: int, page_size: int, public_only: bool = True):
    """Return a bounded reading-history page for exactly one account."""
    where = ["h.user_id = ?"]
    params = [user_id]
    if public_only:
        where.append("b.status='approved' AND b.published_at IS NOT NULL")
    predicate = " AND ".join(where)
    total = query_one(f"SELECT COUNT(*) AS total FROM reading_history h JOIN books b ON b.id=h.book_id WHERE {predicate}", tuple(params))["total"]
    rows = query(
        f"SELECT b.*, h.last_chapter_seq, h.last_read_at FROM reading_history h JOIN books b ON b.id=h.book_id "
        f"WHERE {predicate} ORDER BY h.last_read_at DESC, h.book_id DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size))
    return rows, total


def add_bookmark(user_id: int, book_id: int, chapter_seq: int, position: float, note: str):
    cur = execute("INSERT INTO bookmarks(user_id, book_id, chapter_seq, position, note, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                  (user_id, book_id, chapter_seq, position, note[:500], ts()))
    return cur.lastrowid


def list_bookmarks(user_id: int, book_id: int = None):
    if book_id is None:
        return query("SELECT * FROM bookmarks WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    return query("SELECT * FROM bookmarks WHERE user_id=? AND book_id=? ORDER BY created_at DESC", (user_id, book_id))


def list_bookmarks_page(user_id: int, *, book_id: int = None, page: int, page_size: int):
    """Return a bounded bookmark page; the account predicate is mandatory."""
    where = ["user_id = ?"]
    params = [user_id]
    if book_id is not None:
        where.append("book_id = ?")
        params.append(book_id)
    predicate = " AND ".join(where)
    total = query_one(f"SELECT COUNT(*) AS total FROM bookmarks WHERE {predicate}", tuple(params))["total"]
    rows = query(
        f"SELECT * FROM bookmarks WHERE {predicate} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size))
    return rows, total


def add_notification(user_id: int, kind: str, title: str, body: str = "", link: str = ""):
    # Legacy callers remain source-compatible, but all new rows go through the
    # typed notification service so there is only one writable inbox model.
    from .services import notifications as notification_service
    return notification_service.create_legacy_notification(user_id, kind, title, body, link)


def list_notifications(user_id: int, unread_only: bool = False):
    from .services import notifications as notification_service
    return notification_service.list_notifications(user_id, filter_name="unread" if unread_only else "all")["items"]


def mark_notifications_read(user_id: int, notification_id: int = None):
    from .services import notifications as notification_service
    if notification_id:
        return notification_service.mark_read(user_id, notification_id)
    return notification_service.mark_all_read(user_id)


def add_event(book_id: int, event_type: str, user_id: int = None, session_key: str = None, chapter_seq: int = None, duration: int = 0):
    execute("INSERT INTO book_events(book_id, chapter_seq, user_id, session_key, event_type, duration, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (book_id, chapter_seq, user_id, session_key, event_type, duration, ts()))


def list_rankings(ranking_type: str = "hot", window: str = "7d", category_id: int = None, limit: int = 20):
    params = [ranking_type, window]
    where = "ranking_type=? AND ranking_window=?"
    if category_id:
        where += " AND category_id=?"
        params.append(category_id)
    params.append(max(1, min(limit, 100)))
    rows = query(f"SELECT r.*, b.bid, b.title, b.synopsis, b.cover_path, b.serial, b.chars, b.updated_at, b.category_id, b.category, c.name AS category_name, u.username AS owner FROM ranking_snapshots r JOIN books b ON b.id=r.book_id LEFT JOIN categories c ON c.id=b.category_id LEFT JOIN users u ON u.id=b.owner_id WHERE {where} AND b.status='approved' AND b.published_at IS NOT NULL ORDER BY r.rank ASC LIMIT ?", tuple(params))
    return rows


def refresh_rankings(limit: int = 50):
    now = ts()
    rows = query("SELECT b.id, b.category_id, COALESCE(SUM(CASE WHEN e.event_type IN ('read', 'audio') THEN 1 ELSE 0 END), 0) AS reads, COALESCE(SUM(CASE WHEN e.event_type='complete' THEN 1 ELSE 0 END), 0) AS completions, COALESCE((SELECT COUNT(*) FROM follows f WHERE f.book_id=b.id),0) AS follows, COALESCE((SELECT COUNT(*) FROM library_items li WHERE li.book_id=b.id AND li.kind='favorite'),0) AS favorites FROM books b LEFT JOIN book_events e ON e.book_id=b.id WHERE b.status='approved' AND b.published_at IS NOT NULL GROUP BY b.id ORDER BY (reads * 1.0 + completions * 2.0 + follows * 1.5 + favorites) DESC LIMIT ?", (limit,))
    with WRITE_LOCK:
        con = _conn()
        con.execute("DELETE FROM ranking_snapshots WHERE ranking_type='hot' AND ranking_window='all'")
        for rank, row in enumerate(rows, 1):
            score = float(row["reads"]) + float(row["completions"]) * 2 + float(row["follows"]) * 1.5 + float(row["favorites"])
            con.execute("INSERT INTO ranking_snapshots(ranking_type, ranking_window, category_id, book_id, rank, score, generated_at) VALUES('hot', 'all', ?, ?, ?, ?, ?)", (row["category_id"], row["id"], rank, score, now))
        con.commit()


def service_type_for_job_type(job_type: str) -> str | None:
    if str(job_type or "").startswith("speaker_analysis"):
        return "AI"
    if str(job_type or "").startswith("audio_"):
        return "TTS"
    return None


def _json_object(value: dict | None) -> str:
    try:
        return json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def create_generation_job(job_type: str, book_id: int = None, chapter_id: int = None,
                          requested_by: int = None, payload: dict = None, *,
                          analysis_id: int | None = None,
                          operation_id: int | None = None, service_type: str | None = None,
                          provider: dict | None = None, source_revision: str = "",
                          source_text_hash: str = "", voice_snapshot: dict | None = None,
                          configuration_snapshot: dict | None = None,
                          capability_snapshot: dict | None = None,
                          max_attempts: int = 3):
    """Create a legacy-compatible job with an immutable orchestration snapshot."""
    service_type = service_type or service_type_for_job_type(job_type)
    provider = provider or {}
    provider_id = provider.get("id")
    provider_label = provider.get("name") or provider.get("provider_type") or ""
    provider_model = provider.get("model") or ""
    provider_config = provider.get("config_version")
    if provider_config is None:
        provider_config = provider.get("adapter_key") or ""
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO generation_jobs(book_id, chapter_id, job_type, payload, requested_by, analysis_id, "
            "operation_id, service_type, provider_id, provider_label, provider_model, provider_config_version, "
            "capability_snapshot_json, configuration_snapshot_json, source_revision, source_text_hash, "
            "voice_snapshot_json, max_attempts, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (book_id, chapter_id, job_type, _json_object(payload), requested_by, analysis_id,
             operation_id, service_type, provider_id, provider_label, provider_model, str(provider_config or ""),
             _json_object(capability_snapshot), _json_object(configuration_snapshot), source_revision or "",
             source_text_hash or "", _json_object(voice_snapshot), max(1, int(max_attempts or 3)), ts()),
        )
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return cur.lastrowid


def create_generation_operation(*, book_id: int | None, chapter_id: int | None = None,
                                operation_type: str = "audiobook", requested_by: int | None = None,
                                request_id: int | None = None, source_revision: str = "",
                                source_text_hash: str = "", metadata: dict | None = None) -> int:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO generation_operations(request_id,book_id,chapter_id,operation_type,requested_by,"
            "source_revision,source_text_hash,status,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'queued',?,?,?)",
            (request_id, book_id, chapter_id, operation_type, requested_by, source_revision or "",
             source_text_hash or "", _json_object(metadata), now, now),
        )
        if getattr(_thread, "transaction_depth", 0) == 0:
            con.commit()
        return cur.lastrowid


def _operation_projection(row: dict) -> dict:
    jobs = query("SELECT * FROM generation_jobs WHERE operation_id=? ORDER BY created_at ASC, id ASC",
                 (row["id"],))
    statuses = {str(job.get("status") or "pending") for job in jobs}
    if not jobs:
        status = "queued"
    elif "running" in statuses:
        status = "running"
    elif "waiting_dependency" in statuses:
        status = "waiting_dependency"
    elif "pending" in statuses:
        status = "retrying" if any(job.get("next_attempt_at") for job in jobs) else "queued"
    elif "stale" in statuses:
        status = "stale"
    elif "failed" in statuses:
        status = "failed"
    elif "cancelled" in statuses:
        status = "cancelled"
    elif statuses and statuses <= {"success"}:
        status = "ready"
    else:
        status = "queued"
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    return {
        "id": row["id"], "requestId": row.get("request_id"), "bookId": row.get("book_id"),
        "chapterId": row.get("chapter_id"), "operationType": row.get("operation_type"),
        "requestedBy": row.get("requested_by"), "sourceRevision": row.get("source_revision") or "",
        "sourceTextHash": row.get("source_text_hash") or "", "status": status,
        "authorizationState": metadata.get("authorizationState") or "",
        "errorCategory": row.get("error_category") or "", "error": row.get("error") or "",
        "metadata": metadata, "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
        "startedAt": row.get("started_at"), "finishedAt": row.get("finished_at"),
        "jobs": [{**_safe_generation_job(job), "attemptsHistory": list_generation_attempts(job["id"])} for job in jobs],
    }


def _safe_generation_job(row: dict) -> dict:
    """Serialize operational metadata only; never expose payload secrets/config blobs."""
    provider_id = row.get("provider_id") or row.get("ai_provider_id")
    provider_label = row.get("provider_label") or ""
    if provider_id and not provider_label:
        table = "ai_providers" if (row.get("service_type") or service_type_for_job_type(row.get("job_type"))) == "AI" else "tts_providers"
        provider = query_one(f"SELECT name FROM {table} WHERE id=?", (provider_id,))
        provider_label = (provider or {}).get("name") or ""
    legacy_marker = row.get("legacy_marker") or ""
    if not legacy_marker and row.get("status") in ("success", "failed", "cancelled") and not row.get("attempts"):
        legacy_marker = "LEGACY_PRE_ORCHESTRATION"
    return {
        "id": row["id"], "operationId": row.get("operation_id"), "bookId": row.get("book_id"),
        "chapterId": row.get("chapter_id"), "jobType": row.get("job_type"),
        "serviceType": row.get("service_type") or service_type_for_job_type(row.get("job_type")),
        "status": row.get("status"), "progress": row.get("progress"),
        "attempts": row.get("attempts"), "maxAttempts": row.get("max_attempts"),
        "providerId": provider_id,
        "providerLabel": provider_label,
        "providerModel": row.get("provider_model") or row.get("ai_model") or "",
        "createdAt": row.get("created_at"), "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"), "nextAttemptAt": row.get("next_attempt_at"),
        "leaseUntil": row.get("lease_until"), "heartbeatAt": row.get("heartbeat_at"),
        "cancelRequested": bool(row.get("cancel_requested")),
        "failureCategory": row.get("failure_category") or "", "error": row.get("error") or "",
        "retryable": bool(row.get("retryable")), "legacyMarker": legacy_marker,
    }


def get_generation_operation(operation_id: int, *, safe: bool = True):
    row = query_one("SELECT * FROM generation_operations WHERE id=?", (operation_id,))
    return _operation_projection(row) if row and safe else row


def list_generation_operations(*, service_type: str = "", status: str = "", page: int = 1,
                               page_size: int = 20, approved_only: bool = False) -> dict:
    where, params = [], []
    if service_type in ("AI", "TTS"):
        where.append("EXISTS (SELECT 1 FROM generation_jobs gj WHERE gj.operation_id=go.id AND gj.service_type=?)")
        params.append(service_type)
    if approved_only:
        where.append("EXISTS (SELECT 1 FROM content_requests cr WHERE cr.id=go.request_id AND cr.status='APPROVED')")
    cond = " WHERE " + " AND ".join(where) if where else ""
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page or 1))
    all_rows = query("SELECT go.* FROM generation_operations go" + cond +
                     " ORDER BY go.created_at DESC, go.id DESC", tuple(params))
    projected = [_operation_projection(row) for row in all_rows]
    if status in {"queued", "running", "retrying", "waiting_dependency", "stale", "failed", "cancelled", "ready"}:
        projected = [item for item in projected if item.get("status") == status]
    start = (pg - 1) * per
    items = projected[start:start + per]
    return {"items": items, "total": len(projected), "page": pg, "page_size": per,
            "total_pages": (len(projected) + per - 1) // per if projected else 0}


def create_generation_attempt(job: dict, *, worker_identity: dict, token: str,
                              service_type: str, provider_id: int | None, provider_label: str,
                              provider_model: str, provider_config_version: str,
                              started_at: str) -> int:
    cur = _conn().execute(
        "INSERT INTO generation_job_attempts(job_id,attempt_number,service_type,provider_id,provider_label,"
        "provider_model,provider_config_version,worker_instance_id,worker_claim_token,started_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (job["id"], int(job.get("attempts") or 0) + 1, service_type or "UNKNOWN", provider_id,
         provider_label or "", provider_model or "", provider_config_version or "",
         worker_identity["instance_id"], token, started_at, started_at, started_at),
    )
    return cur.lastrowid


def finish_generation_attempt(attempt_id: int | None, *, outcome: str, failure_category: str = "",
                              retryable: bool = False, diagnostics: dict | None = None,
                              finished_at: str | None = None) -> bool:
    if not attempt_id:
        return False
    now = finished_at or ts()
    cur = execute(
        "UPDATE generation_job_attempts SET finished_at=?, outcome=?, failure_category=?, retryable=?, "
        "diagnostics=?, updated_at=? WHERE id=? AND finished_at IS NULL",
        (now, outcome, (failure_category or "")[:100], 1 if retryable else 0,
         _json_object(diagnostics), now, attempt_id),
    )
    return cur.rowcount == 1


def list_generation_attempts(job_id: int) -> list[dict]:
    rows = query("SELECT id,job_id,attempt_number,service_type,provider_id,provider_label,provider_model,"
                 "provider_config_version,worker_instance_id,started_at,finished_at,outcome,failure_category,"
                 "retryable,diagnostics,created_at,updated_at FROM generation_job_attempts "
                 "WHERE job_id=? ORDER BY attempt_number ASC", (job_id,))
    for row in rows:
        try:
            row["diagnostics"] = json.loads(row.get("diagnostics") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            row["diagnostics"] = {}
    return rows


def get_generation_job(job_id: int):
    return query_one("SELECT * FROM generation_jobs WHERE id=?", (job_id,))


def get_active_analysis_job(chapter_id: int):
    """回傳章節目前可取消的最新 speaker-analysis job。"""
    return query_one(
        "SELECT * FROM generation_jobs "
        "WHERE chapter_id=? AND job_type='speaker_analysis' "
        "AND status IN ('pending','running') AND analysis_id IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (chapter_id,),
    )


def get_active_analysis_batch_job(book_id: int):
    """回傳作品目前可取消的最新全書 speaker-analysis job。"""
    return query_one(
        "SELECT * FROM generation_jobs "
        "WHERE book_id=? AND job_type='speaker_analysis_all' "
        "AND status IN ('pending','running') "
        "ORDER BY id DESC LIMIT 1",
        (book_id,),
    )


def list_orphaned_analysis_jobs(*, stale_after_seconds: int = 90) -> list[dict]:
    """找出 owner 已消失的 running analysis jobs，不重用原 job。

    有 worker process identity 時，以 process 是否仍存在為主要證據；不能
    只依 heartbeat，因單一 worker 執行長 provider request 時 heartbeat 可能
    暫停。沒有 identity 的舊資料則只在超過 bounded age 後視為 stale。
    """
    cutoff = (datetime.now() - timedelta(seconds=max(1, int(stale_after_seconds)))).isoformat(
        timespec="seconds")
    rows = query(
        "SELECT j.*, w.active AS owner_active, w.process_id AS owner_process_id, "
        "w.heartbeat_at AS owner_heartbeat_at "
        "FROM generation_jobs j LEFT JOIN worker_instances w "
        "ON w.instance_id=j.worker_instance_id "
        "WHERE j.job_type IN ('speaker_analysis', 'speaker_analysis_all') "
        "AND j.status='running' "
        "AND (j.job_type='speaker_analysis_all' OR j.analysis_id IS NOT NULL) "
        "ORDER BY j.id"
    )
    orphaned = []
    for row in rows:
        instance_id = row.get("worker_instance_id")
        if instance_id:
            owner_alive = bool(row.get("owner_active")) and _process_is_alive(
                row.get("owner_process_id"))
            if owner_alive:
                continue
            reason = "worker_lost" if row.get("owner_process_id") else "stale_job_claim"
        else:
            if not row.get("started_at") or row["started_at"] >= cutoff:
                continue
            reason = "stale_job_claim"
        item = dict(row)
        item["orphan_reason"] = reason
        orphaned.append(item)
    return orphaned


def create_analysis_job(job_type: str, *, book_id: int, chapter_id: int | None,
                        analysis_id: int | None, provider: dict, requested_by: int | None,
                        payload: dict | None = None) -> int:
    """建立綁定 AI provider 快照的 V4 analysis job，回傳 job id。"""
    job_id = create_generation_job(
        job_type, book_id=book_id, chapter_id=chapter_id, requested_by=requested_by,
        payload=payload, provider=provider, analysis_id=analysis_id,
        service_type="AI" if str(job_type).startswith("speaker_") else "TTS",
        source_text_hash=((get_chapter(book_id, int((payload or {}).get("seq"))) or {}).get("text_hash", "")
                          if chapter_id is not None and (payload or {}).get("seq") is not None else ""),
        configuration_snapshot={"kind": "analysis", "configVersion": provider.get("config_version")},
    )
    with WRITE_LOCK:
        con = _conn()
        con.execute(
            "UPDATE generation_jobs SET analysis_id=?, ai_provider_id=?, ai_model=?, ai_config_version=?, "
            "provider_id=COALESCE(provider_id,?), provider_label=COALESCE(NULLIF(provider_label,''),?), "
            "provider_model=COALESCE(NULLIF(provider_model,''),?), provider_config_version=COALESCE(NULLIF(provider_config_version,''),?) "
            "WHERE id=?",
            (analysis_id, provider.get("id"), provider.get("model"), provider.get("config_version"),
             provider.get("id"), provider.get("name") or provider.get("provider_type") or "",
             provider.get("model") or "", str(provider.get("config_version") or ""), job_id),
        )
        con.commit()
    return job_id


def list_generation_jobs(limit: int = 100):
    return query("SELECT * FROM generation_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),))


def list_generation_jobs_paged(*, status: str = "", job_type: str = "",
                               page: int = 1, page_size: int = 20):
    """管理員任務清單：狀態/類型篩選 + server-side 分頁。"""
    where, params = [], []
    if status in ("pending", "running", "success", "failed", "cancelled"):
        where.append("status = ?")
        params.append(status)
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = query_one(f"SELECT COUNT(*) n FROM generation_jobs{cond}", tuple(params))["n"]
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page))
    rows = query(
        f"SELECT * FROM generation_jobs{cond} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per))
    return {"items": rows, "total": total, "page": pg, "page_size": per,
            "total_pages": (total + per - 1) // per if total else 0}


JOB_TERMINAL_STATUSES = ("success", "failed", "cancelled")
DEFAULT_GENERATION_LEASE_SECONDS = 120
GENERATION_HEARTBEAT_INTERVAL_SECONDS = 10


def delete_generation_job(job_id: int) -> bool:
    """刪除單筆 job row（僅 terminal 狀態）；回傳是否真的刪除。不影響 audio/generation 產物。"""
    row = query_one("SELECT status FROM generation_jobs WHERE id=?", (job_id,))
    if not row:
        return False
    if row["status"] not in JOB_TERMINAL_STATUSES:
        raise ValueError(f"只能清除已完成/失敗/已取消的任務（目前狀態：{row['status']}）")
    execute("DELETE FROM generation_jobs WHERE id=?", (job_id,))
    return True


def clear_generation_jobs_by_status(status: str) -> int:
    """批次清除特定 terminal 狀態的 job history；回傳刪除筆數。"""
    if status not in JOB_TERMINAL_STATUSES:
        raise ValueError("只能批次清除 success/failed/cancelled 狀態的任務")
    return execute("DELETE FROM generation_jobs WHERE status=?", (status,)).rowcount


def _process_is_alive(process_id: int | None) -> bool:
    """跨平台唯讀檢查 PID；Windows 不可用 os.kill(pid, 0) 探測。"""
    if not process_id:
        return False
    try:
        pid = int(process_id)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def register_worker_instance(*, instance_id: str, worker_version: str, build_sha: str,
                             process_id: int | None, db_path: str, worker_enabled: bool,
                             service_types: str = "AI,TTS",
                             stale_after_seconds: int = 90) -> bool:
    """註冊 worker；不同 build 的活躍 consumer 不得共用同一 queue。"""
    cutoff = (datetime.now() - timedelta(seconds=stale_after_seconds)).isoformat(timespec="seconds")
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        # 強制終止的舊 process 來不及做 graceful unregister；先以 PID/heartbeat
        # 清掉可證明已不存在的 registry residue，避免健康檢查誤報多個 active worker。
        for row in con.execute(
            "SELECT instance_id, process_id, heartbeat_at FROM worker_instances WHERE active=1").fetchall():
            alive = _process_is_alive(row["process_id"])
            if not alive and row["instance_id"] != instance_id:
                con.execute("UPDATE worker_instances SET active=0 WHERE instance_id=?", (row["instance_id"],))
        con.execute("UPDATE worker_instances SET active=0 WHERE active=1 AND heartbeat_at < ?", (cutoff,))
        conflict = con.execute(
            "SELECT instance_id, worker_version, build_sha FROM worker_instances "
            "WHERE active=1 AND instance_id<>? AND heartbeat_at>=? "
            "AND (worker_version<>? OR build_sha<>?) LIMIT 1",
            (instance_id, cutoff, worker_version, build_sha),
        ).fetchone()
        if conflict:
            con.commit()
            return False
        con.execute(
            "INSERT INTO worker_instances(instance_id, worker_version, build_sha, service_types, process_id, db_path, "
            "started_at, heartbeat_at, active, worker_enabled) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ? ) "
            "ON CONFLICT(instance_id) DO UPDATE SET worker_version=excluded.worker_version, "
            "build_sha=excluded.build_sha, service_types=excluded.service_types, process_id=excluded.process_id, db_path=excluded.db_path, "
            "heartbeat_at=excluded.heartbeat_at, active=1, worker_enabled=excluded.worker_enabled",
            (instance_id, worker_version, build_sha, service_types, process_id, db_path, now, now, 1 if worker_enabled else 0),
        )
        con.commit()
        return True


def heartbeat_worker_instance(instance_id: str) -> None:
    execute("UPDATE worker_instances SET heartbeat_at=?, active=1 WHERE instance_id=?", (ts(), instance_id))


def unregister_worker_instance(instance_id: str) -> None:
    execute("UPDATE worker_instances SET active=0, heartbeat_at=? WHERE instance_id=?", (ts(), instance_id))


def list_worker_instances(*, active_only: bool = False) -> list[dict]:
    where = " WHERE active=1" if active_only else ""
    return query(f"SELECT * FROM worker_instances{where} ORDER BY started_at DESC")


class GenerationOwnershipConflict(RuntimeError):
    """A stale worker attempted to mutate a job it no longer owns."""


def _provider_capacity(con, service_type: str, provider_id: int | None) -> int:
    if not provider_id:
        return 1
    table = "ai_providers" if service_type == "AI" else "tts_providers"
    row = con.execute(f"SELECT max_concurrency, enabled, "
                      f"health_state, cooldown_until FROM {table} WHERE id=?", (provider_id,)).fetchone()
    if not row or not bool(row["enabled"]):
        return 0
    if row["health_state"] in ("cooldown", "disabled") and row["cooldown_until"] and row["cooldown_until"] > ts():
        return 0
    return max(1, min(int(row["max_concurrency"] or 1), 100))


def _service_capacity(service_type: str | None) -> int:
    # Environment configuration is intentionally process-local and safe for
    # development.  Provider capacity remains the durable per-provider limit.
    key = "GENERATION_AI_MAX_CONCURRENCY" if service_type == "AI" else "GENERATION_TTS_MAX_CONCURRENCY"
    try:
        return max(1, min(int(os.environ.get(key, "1")), 100))
    except (TypeError, ValueError):
        return 1


def claim_generation_job(worker_identity: dict | None = None, *, service_type: str | None = None,
                         provider_id: int | None = None, lease_seconds: int = DEFAULT_GENERATION_LEASE_SECONDS,
                         now: str | None = None, ignore_schedule: bool = False):
    """Atomically claim one typed job and create its immutable attempt row."""
    import uuid
    legacy_direct_claim = worker_identity is None and service_type is None
    identity = worker_identity or {
        "instance_id": "direct-db-claim",
        "worker_version": "direct-db-claim",
        "build_sha": "direct-db-claim",
    }
    service_type = service_type.upper() if service_type else None
    if service_type not in (None, "AI", "TTS"):
        raise ValueError("service_type must be AI or TTS")
    now = now or ts()
    try:
        lease_until = (datetime.fromisoformat(now) + timedelta(seconds=max(5, int(lease_seconds)))).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        lease_until = ts()
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "UPDATE generation_jobs SET status='stale', progress=100, error='來源 revision 已變更，請重新建立生成操作', "
                "failure_category='source_changed', retryable=0, finished_at=? "
                "WHERE status='pending' AND cancel_requested=0 AND chapter_id IS NOT NULL AND source_text_hash<>'' "
                "AND EXISTS (SELECT 1 FROM chapters c WHERE c.id=generation_jobs.chapter_id AND c.text_hash<>generation_jobs.source_text_hash)",
                (now,),
            )
            clauses = ["status IN ('pending','waiting_dependency')", "cancel_requested=0"]
            params: list = []
            if not ignore_schedule:
                clauses.append("(next_attempt_at IS NULL OR next_attempt_at<=?)")
                params.append(now)
            if service_type:
                clauses.append("(service_type=? OR (service_type IS NULL AND "
                               "((?='AI' AND job_type LIKE 'speaker_analysis%') OR "
                               "(?='TTS' AND job_type LIKE 'audio_%'))))")
                params.extend([service_type, service_type, service_type])
            if provider_id:
                clauses.append("COALESCE(provider_id, ai_provider_id)=?")
                params.append(provider_id)
            candidates = con.execute(
                "SELECT * FROM generation_jobs WHERE " + " AND ".join(clauses) +
                " ORDER BY CASE WHEN next_attempt_at IS NULL THEN 0 ELSE 1 END, "
                "COALESCE(next_attempt_at, created_at) ASC, created_at ASC, id ASC LIMIT 100", tuple(params)
            ).fetchall()
            job = None
            job_service = None
            bound_provider_id = None
            for candidate in candidates:
                candidate = dict(candidate)
                if candidate.get("status") == "waiting_dependency":
                    try:
                        from .services import generation_orchestration as orchestration
                        if not orchestration.dependencies_ready(candidate):
                            continue
                    except Exception:
                        # A dependency read failure is fail-closed: leave the
                        # job waiting and allow unrelated queue work through.
                        continue
                    promoted = con.execute(
                        "UPDATE generation_jobs SET status='pending', error='', failure_category='', "
                        "retryable=0, next_attempt_at=NULL WHERE id=? AND status='waiting_dependency'",
                        (candidate["id"],),
                    )
                    if promoted.rowcount != 1:
                        continue
                    candidate["status"] = "pending"
                candidate_service = service_type or candidate["service_type"] or service_type_for_job_type(candidate["job_type"])
                if not candidate_service:
                    # Unknown legacy test jobs remain claimable by the
                    # compatibility path, but typed consumers never claim
                    # them because their query already filters service type.
                    candidate_service = "UNKNOWN"
                candidate_provider_id = candidate["provider_id"] or candidate["ai_provider_id"]
                capacity = _service_capacity(candidate_service) if candidate_service in ("AI", "TTS") else 100
                active_service = con.execute(
                    "SELECT COUNT(*) AS n FROM generation_jobs WHERE status='running' AND cancel_requested=0 "
                    "AND COALESCE(service_type, CASE WHEN job_type LIKE 'speaker_analysis%' THEN 'AI' "
                    "WHEN job_type LIKE 'audio_%' THEN 'TTS' ELSE '' END)=?", (candidate_service,)).fetchone()["n"]
                if int(active_service) >= capacity:
                    continue
                if candidate_provider_id:
                    provider_capacity = _provider_capacity(con, candidate_service, candidate_provider_id)
                    # Existing synchronous test/maintenance callers historically
                    # created jobs with a provider-shaped placeholder but no
                    # provider row.  Keep that compatibility path claimable so
                    # the executor can return its explicit unavailable/config
                    # error; real typed consumers fail closed before execution.
                    if legacy_direct_claim and provider_capacity == 0:
                        provider_capacity = 1
                    if provider_capacity <= 0:
                        continue
                    active_provider = con.execute(
                        "SELECT COUNT(*) AS n FROM generation_jobs WHERE status='running' AND cancel_requested=0 "
                        "AND COALESCE(provider_id, ai_provider_id)=? AND COALESCE(service_type, "
                        "CASE WHEN job_type LIKE 'speaker_analysis%' THEN 'AI' WHEN job_type LIKE 'audio_%' THEN 'TTS' ELSE '' END)=?",
                        (candidate_provider_id, candidate_service)).fetchone()["n"]
                    if int(active_provider) >= provider_capacity:
                        continue
                job = candidate
                job_service = candidate_service
                bound_provider_id = candidate_provider_id
                break
            if not job:
                con.rollback()
                return None
            token = uuid.uuid4().hex
            attempt_number = int(job["attempts"] or 0) + 1
            attempt_id = con.execute(
                "INSERT INTO generation_job_attempts(job_id,attempt_number,service_type,provider_id,provider_label,"
                "provider_model,provider_config_version,worker_instance_id,worker_claim_token,started_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (job["id"], attempt_number, job_service, bound_provider_id, job["provider_label"] or "",
                 job["provider_model"] or job["ai_model"] or "", job["provider_config_version"] or str(job["ai_config_version"] or ""),
                 identity["instance_id"], token, now, now, now),
            ).lastrowid
            cur = con.execute(
                "UPDATE generation_jobs SET status='running', attempts=attempts+1, started_at=?, claimed_at=?, "
                "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=?, "
                "service_type=COALESCE(service_type,?), provider_id=COALESCE(provider_id,ai_provider_id), "
                "lease_until=?, heartbeat_at=?, attempt_id=?, cancel_requested=0, finished_at=NULL "
                "WHERE id=? AND status='pending' AND cancel_requested=0",
                (now, now, identity["instance_id"], identity["worker_version"],
                 identity.get("build_sha", identity["worker_version"]), token, job_service,
                 lease_until, now, attempt_id, job["id"]),
            )
            if cur.rowcount != 1:
                con.rollback()
                return None
            con.commit()
            return dict(con.execute("SELECT * FROM generation_jobs WHERE id=?", (job["id"],)).fetchone())
        except Exception:
            con.rollback()
            raise


def heartbeat_generation_job(job_id: int, claim_token: str, *, lease_seconds: int = DEFAULT_GENERATION_LEASE_SECONDS) -> bool:
    now = ts()
    lease_until = (datetime.fromisoformat(now) + timedelta(seconds=max(5, int(lease_seconds)))).isoformat(timespec="seconds")
    cur = execute(
        "UPDATE generation_jobs SET heartbeat_at=?, lease_until=? WHERE id=? AND status='running' "
        "AND worker_claim_token=? AND cancel_requested=0",
        (now, lease_until, job_id, claim_token),
    )
    return cur.rowcount == 1


def update_generation_job(job_id: int, status: str, progress: int = None, error: str = None, *,
                          claim_token: str | None = None, failure_category: str | None = None,
                          retryable: bool | None = None, expected_status: str | None = None) -> bool:
    fields = {"status": status}
    if status == "running":
        # 維護/測試路徑可能直接標記 running；仍留下可辨識的 claim metadata。
        # 真正的 queue claim 由 claim_generation_job() 產生 process identity/token，
        # 舊 worker 則會在 pending → running 的 DB trigger 被拒絕。
        current = query_one(
            "SELECT worker_instance_id, worker_version, worker_claim_token "
            "FROM generation_jobs WHERE id=?", (job_id,))
        if current and not current.get("worker_claim_token"):
            now = ts()
            fields.update({
                "worker_instance_id": "maintenance",
                "worker_version": f"{settings.APP_VERSION}:maintenance",
                "worker_build_sha": "maintenance",
                "claimed_at": now,
                "worker_claim_token": uuid.uuid4().hex,
            })
    if progress is not None:
        fields["progress"] = max(0, min(100, int(progress)))
    if error is not None:
        fields["error"] = error[:2000]
    if status in ("success", "failed", "cancelled"):
        fields["finished_at"] = ts()
    sets = ", ".join("progress=MAX(progress,?)" if key == "progress" else f"{key}=?" for key in fields)
    where = "id=?"
    params = [*fields.values(), job_id]
    if claim_token:
        where += " AND status='running' AND worker_claim_token=?"
        params.append(claim_token)
    if expected_status:
        where += " AND status=?"
        params.append(expected_status)
    cur = execute(f"UPDATE generation_jobs SET {sets} WHERE {where}", tuple(params))
    if claim_token and cur.rowcount != 1:
        return False
    return cur.rowcount == 1


def finalize_generation_job(job_id: int, claim_token: str, status: str, *, progress: int = 100,
                            error: str = "", failure_category: str = "", retryable: bool = False,
                            diagnostics: dict | None = None) -> bool:
    if status not in ("success", "failed", "cancelled"):
        raise ValueError("final job status must be terminal")
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM generation_jobs WHERE id=? AND status='running' "
                           "AND worker_claim_token=?", (job_id, claim_token)).fetchone()
        if not row:
            con.rollback()
            return False
        now = ts()
        final_status = "cancelled" if row["cancel_requested"] else status
        cur = con.execute(
            "UPDATE generation_jobs SET status=?, progress=?, error=?, failure_category=?, retryable=?, "
            "finished_at=?, lease_until=NULL, heartbeat_at=? "
            "WHERE id=? AND status='running' AND worker_claim_token=?",
            (final_status, max(0, min(100, int(progress))), (error or "")[:2000], (failure_category or "")[:100],
             1 if retryable else 0, now, now, job_id, claim_token),
        )
        if cur.rowcount != 1:
            con.rollback()
            return False
        con.execute(
            "UPDATE generation_job_attempts SET finished_at=?, outcome=?, failure_category=?, retryable=?, "
            "diagnostics=?, updated_at=? WHERE id=? AND worker_claim_token=? AND finished_at IS NULL",
            (now, final_status, (failure_category or "")[:100], 1 if retryable else 0,
             _json_object(diagnostics), now, row["attempt_id"], claim_token),
        )
        con.commit()
        return True


def update_generation_job_usage_metrics(job_id: int, usage_metrics: dict, *, claim_token: str | None = None):
    current = query_one("SELECT usage_metrics_json FROM generation_jobs WHERE id=?", (job_id,))
    try:
        aggregate = json.loads((current or {}).get("usage_metrics_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        aggregate = {}
    incoming = usage_metrics or {}
    for key, value in incoming.items():
        if key == "schemaVersion":
            continue
        if key == "usageAvailable":
            aggregate[key] = bool(aggregate.get(key) or value)
        else:
            try:
                aggregate[key] = max(int(aggregate.get(key) or 0), int(value or 0))
            except (TypeError, ValueError):
                pass
    aggregate["schemaVersion"] = incoming.get("schemaVersion", aggregate.get("schemaVersion", "ai-usage-v1"))
    if claim_token:
        execute(
            "UPDATE generation_jobs SET usage_metrics_json=? WHERE id=? AND status='running' AND worker_claim_token=?",
            (json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")), job_id, claim_token),
        )
    else:
        execute(
            "UPDATE generation_jobs SET usage_metrics_json=? WHERE id=?",
            (json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")), job_id),
        )


def requeue_generation_job(job_id: int, error: str = "", *, next_attempt_at: str | None = None,
                           failure_category: str = "", retryable: bool = True,
                           claim_token: str | None = None) -> bool:
    """Requeue a bounded retry without erasing attempt history."""
    where = "id=? AND status='running'"
    params = ["pending", 0, (error or "")[:2000], (failure_category or "")[:100],
              1 if retryable else 0, next_attempt_at, None]
    params.append(job_id)
    if claim_token:
        where += " AND worker_claim_token=?"
        params.append(claim_token)
    cur = execute(
        "UPDATE generation_jobs SET status=?, progress=?, error=?, failure_category=?, retryable=?, "
        "next_attempt_at=?, finished_at=?, started_at=NULL, claimed_at=NULL, worker_instance_id=NULL, "
        "worker_version=NULL, worker_build_sha=NULL, worker_claim_token=NULL, lease_until=NULL, "
        "heartbeat_at=NULL, attempt_id=NULL WHERE " + where,
        tuple(params),
    )
    # Compatibility callers may requeue a row already reset by legacy code.
    if cur.rowcount == 0 and not claim_token:
        cur = execute(
            "UPDATE generation_jobs SET status='pending', progress=0, error=?, failure_category=?, retryable=?, "
            "next_attempt_at=?, finished_at=NULL, started_at=NULL, claimed_at=NULL, worker_instance_id=NULL, "
            "worker_version=NULL, worker_build_sha=NULL, worker_claim_token=NULL, lease_until=NULL, heartbeat_at=NULL, attempt_id=NULL "
            "WHERE id=? AND status IN ('pending','failed')",
            ((error or "")[:2000], (failure_category or "")[:100], 1 if retryable else 0, next_attempt_at, job_id),
        )
    return cur.rowcount == 1


def mark_generation_job_waiting_dependency(job_id: int, error: str = "等待必要前置條件") -> bool:
    """Keep non-executable TTS work out of the runnable queue."""
    cur = execute(
        "UPDATE generation_jobs SET status='waiting_dependency', progress=0, error=?, "
        "failure_category='waiting_dependency', retryable=0, next_attempt_at=NULL "
        "WHERE id=? AND status='pending' AND attempts=0",
        ((error or "等待必要前置條件")[:2000], job_id),
    )
    return cur.rowcount == 1


def request_generation_job_cancel(job_id: int, *, actor_id: int | None = None) -> str:
    """Atomically cancel queued work or request cooperative cancellation."""
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            con.rollback()
            raise KeyError(job_id)
        if row["status"] in JOB_TERMINAL_STATUSES:
            con.rollback()
            return "already_terminal"
        now = ts()
        if row["status"] == "pending":
            cur = con.execute(
                "UPDATE generation_jobs SET status='cancelled', progress=100, error=?, finished_at=?, "
                "cancel_requested=1, next_attempt_at=NULL WHERE id=? AND status='pending' AND cancel_requested=0",
                ("已由操作員取消", now, job_id),
            )
            result = "cancelled" if cur.rowcount == 1 else "conflict"
        elif row["status"] == "running":
            cur = con.execute(
                "UPDATE generation_jobs SET cancel_requested=1, error=? WHERE id=? AND status='running'",
                ("已要求取消；等待目前執行單元安全結束", job_id),
            )
            result = "requested" if cur.rowcount == 1 else "conflict"
        else:
            result = "conflict"
        con.commit()
        if result == "cancelled" and actor_id:
            add_audit_log(actor_id, "generation_job_cancelled", "generation_job", job_id, {})
        elif result == "requested" and actor_id:
            add_audit_log(actor_id, "generation_job_cancel_requested", "generation_job", job_id, {})
        if result == "cancelled" and actor_id:
            from .services import notifications as notification_service
            try:
                notification_service.notify_generation_terminal(job_id, "cancelled", actor_id=actor_id)
            except Exception:
                # The cancelled job remains terminal; startup recovery can
                # repair the user-facing notification without reopening it.
                pass
        return result


def retry_generation_job(job_id: int, *, actor_id: int | None = None) -> bool:
    """Explicit operator retry; failed history stays immutable."""
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] not in ("failed", "stale") or row["failure_category"] == "source_changed":
            con.rollback()
            return False
        cur = con.execute(
            "UPDATE generation_jobs SET status='pending', progress=0, error='', failure_category='', retryable=0, "
            "next_attempt_at=?, finished_at=NULL, cancel_requested=0 WHERE id=? AND status IN ('failed','stale')",
            (ts(), job_id),
        )
        con.commit()
    if cur.rowcount == 1 and actor_id:
        add_audit_log(actor_id, "generation_job_retried", "generation_job", job_id, {})
    return cur.rowcount == 1


def recover_expired_generation_jobs(*, now: str | None = None, stale_after_seconds: int = 3600) -> int:
    """Recover expired leases; late workers are fenced by their old token."""
    now = now or ts()
    cutoff = (datetime.now() - timedelta(seconds=max(1, int(stale_after_seconds)))).isoformat(timespec="seconds")
    changed = 0
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT * FROM generation_jobs WHERE status='running' AND "
            "((lease_until IS NOT NULL AND lease_until<?) OR (lease_until IS NULL AND started_at<?))",
            (now, cutoff),
        ).fetchall()
        for row in rows:
            # Legacy rows with a live registered owner are left alone. New
            # leased rows are only reclaimable after lease expiry.
            if not row["lease_until"] and row["worker_instance_id"]:
                owner = con.execute("SELECT active,process_id FROM worker_instances WHERE instance_id=?",
                                    (row["worker_instance_id"],)).fetchone()
                if owner and bool(owner["active"]) and _process_is_alive(owner["process_id"]):
                    continue
            attempt_id = row["attempt_id"]
            con.execute(
                "UPDATE generation_job_attempts SET finished_at=?, outcome='worker_lost', failure_category='worker_lost', "
                "retryable=1, diagnostics=?, updated_at=? WHERE id=? AND finished_at IS NULL",
                (now, _json_object({"reason": "lease_expired"}), now, attempt_id),
            )
            if int(row["attempts"] or 0) < int(row["max_attempts"] or 3):
                con.execute(
                    "UPDATE generation_jobs SET status='pending', progress=0, error=?, failure_category='worker_lost', "
                    "retryable=1, next_attempt_at=?, started_at=NULL, claimed_at=NULL, worker_instance_id=NULL, "
                    "worker_version=NULL, worker_build_sha=NULL, worker_claim_token=NULL, lease_until=NULL, heartbeat_at=NULL, attempt_id=NULL "
                    "WHERE id=? AND status='running' AND worker_claim_token=?",
                    ("worker lease 已逾時，工作重新排隊", now, row["id"], row["worker_claim_token"]),
                )
            else:
                con.execute(
                    "UPDATE generation_jobs SET status='failed', progress=100, error=?, failure_category='worker_lost', "
                    "retryable=0, finished_at=?, lease_until=NULL, heartbeat_at=? WHERE id=? AND status='running' AND worker_claim_token=?",
                    ("worker lease 已逾時，已達重試上限", now, now, row["id"], row["worker_claim_token"]),
                )
            changed += 1
        con.commit()
    return changed


def requeue_stale_generation_jobs(max_age_seconds: int = 3600) -> int:
    return recover_expired_generation_jobs(stale_after_seconds=max_age_seconds)


def list_tts_providers():
    return query("SELECT * FROM tts_providers ORDER BY name COLLATE NOCASE")


def get_tts_provider(provider_id: int):
    return query_one("SELECT * FROM tts_providers WHERE id=?", (provider_id,))


def create_tts_provider(data: dict):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO tts_providers(name, provider_type, base_url, synth_path, voices_path, auth_scheme, secret_ciphertext, enabled, is_default, adapter_key, timeout_seconds, config_version, max_concurrency, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (data["name"], data.get("provider_type", "generic_http"), data["base_url"],
             data.get("synth_path", "/synthesize"), data.get("voices_path", "/voices"),
             data.get("auth_scheme", "bearer"), data.get("secret_ciphertext", ""),
             1 if data.get("enabled", True) else 0, 1 if data.get("is_default", False) else 0,
             data.get("adapter_key", "generic_http"), data.get("timeout_seconds", 95),
             max(1, min(int(data.get("max_concurrency", 1) or 1), 100)), now, now),
        )
        con.commit()
        return cur.lastrowid


def update_tts_provider(provider_id: int, fields: dict):
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = ts()
    # Provider configuration is part of the TTS partial/generation identity.
    # A changed endpoint, credential, adapter, or timeout must therefore
    # invalidate previously rendered provider output.  Operational fields
    # such as capability checks and status updates must not do so.
    config_fields = {
        "name", "provider_type", "base_url", "synth_path", "voices_path",
        "auth_scheme", "secret_ciphertext", "adapter_key", "timeout_seconds", "max_concurrency",
        "enabled",
    }
    with WRITE_LOCK:
        con = _conn()
        if config_fields & set(fields):
            current = query_one(
                "SELECT config_version FROM tts_providers WHERE id=?",
                (provider_id,),
            ) or {}
            fields["config_version"] = int(current.get("config_version") or 0) + 1
        sets = ", ".join(f"{key}=?" for key in fields)
        con.execute(f"UPDATE tts_providers SET {sets} WHERE id=?", (*fields.values(), provider_id))
        con.commit()


def delete_tts_provider(provider_id: int):
    execute("DELETE FROM tts_providers WHERE id=?", (provider_id,))


def get_active_tts_provider():
    return query_one(
        "SELECT * FROM tts_providers WHERE enabled=1 ORDER BY is_default DESC, id ASC LIMIT 1"
    )


def list_tts_provider_voices(provider_id: int = None, include_unavailable: bool = False):
    status = "" if include_unavailable else " AND enabled=1 AND catalog_status='available'"
    if provider_id is None:
        return query(f"SELECT * FROM tts_provider_voices WHERE 1=1{status} ORDER BY name COLLATE NOCASE")
    return query(f"SELECT * FROM tts_provider_voices WHERE provider_id=?{status} ORDER BY name COLLATE NOCASE", (provider_id,))


def replace_tts_provider_voices(provider_id: int, voices: list[dict]):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        seen = set()
        for voice in voices[:500]:
            voice_id = str(voice.get("id") or voice.get("voice_id") or "").strip()
            if not voice_id:
                continue
            seen.add(voice_id)
            raw_languages = voice.get("languages")
            if isinstance(raw_languages, str):
                raw_languages = [raw_languages]
            if not isinstance(raw_languages, list):
                raw_languages = []
            languages = []
            for language in raw_languages:
                value = str(language or "").strip()[:20]
                if value and value not in languages:
                    languages.append(value)
            legacy_lang = str(voice.get("lang") or voice.get("language") or "").strip()[:20]
            if not languages and legacy_lang:
                languages = [legacy_lang]
            if not legacy_lang and languages:
                legacy_lang = languages[0].split("-")[0].split("_")[0][:20]
            raw_capabilities = voice.get("expressive")
            if raw_capabilities is None:
                raw_capabilities = voice.get("expressiveCapability")
            if raw_capabilities is None:
                raw_capabilities = voice.get("capabilities")
            if not isinstance(raw_capabilities, dict):
                raw_capabilities = {}
            raw_age = voice.get("age")
            age = None if raw_age is None else str(raw_age).strip()[:40] or None
            con.execute(
                "INSERT INTO tts_provider_voices(provider_id, voice_id, name, lang, gender, age, region, languages_json, enabled, catalog_status, capabilities_json, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, 'available', ?, ?) "
                "ON CONFLICT(provider_id,voice_id) DO UPDATE SET name=excluded.name, lang=excluded.lang, gender=excluded.gender, age=excluded.age, region=excluded.region, languages_json=excluded.languages_json, enabled=1, catalog_status='available', capabilities_json=excluded.capabilities_json, updated_at=excluded.updated_at",
                (provider_id, voice_id, str(voice.get("display_name") or voice.get("name") or voice_id)[:120], legacy_lang, str(voice.get("gender") or "")[:20], age, str(voice.get("region") or "remote")[:20], json.dumps(languages, ensure_ascii=False), json.dumps(raw_capabilities, ensure_ascii=False), now),
            )
        if seen:
            marks = ",".join("?" for _ in seen)
            con.execute(f"UPDATE tts_provider_voices SET enabled=0, catalog_status='unavailable', updated_at=? WHERE provider_id=? AND voice_id NOT IN ({marks})", (now, provider_id, *seen))
        else:
            con.execute("UPDATE tts_provider_voices SET enabled=0, catalog_status='unavailable', updated_at=? WHERE provider_id=?", (now, provider_id))
        if seen:
            profile_marks = ",".join("?" for _ in seen)
            con.execute(
                f"UPDATE expressive_profiles SET availability_status=CASE WHEN voice_id IN ({profile_marks}) THEN 'available' ELSE 'stale' END, updated_at=? WHERE status='active'",
                (*seen, now),
            )
        else:
            con.execute("UPDATE expressive_profiles SET availability_status='stale', updated_at=? WHERE status='active'", (now,))
        con.commit()


def list_ai_providers():
    return query("SELECT * FROM ai_providers WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE")


def find_ai_provider_by_name(name: str, exclude_id: int | None = None):
    if exclude_id is None:
        return query_one("SELECT * FROM ai_providers WHERE deleted_at IS NULL AND lower(name)=lower(?) LIMIT 1", (name,))
    return query_one("SELECT * FROM ai_providers WHERE deleted_at IS NULL AND lower(name)=lower(?) AND id<>? LIMIT 1", (name, exclude_id))


def get_ai_provider(provider_id: int):
    return query_one("SELECT * FROM ai_providers WHERE id=? AND deleted_at IS NULL", (provider_id,))


def create_ai_provider(data: dict):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        if data.get("is_default"):
            con.execute("UPDATE ai_providers SET is_default=0 WHERE deleted_at IS NULL")
        cur = con.execute(
            "INSERT INTO ai_providers(name, provider_type, base_url, model, fallback_model, secret_ciphertext, enabled, is_default, config_version, max_concurrency, created_by, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (data["name"], data["provider_type"], data["base_url"], data["model"],
             data.get("fallback_model"), data.get("secret_ciphertext", ""),
             1 if data.get("enabled", True) else 0, 1 if data.get("is_default", False) else 0,
              max(1, min(int(data.get("max_concurrency", 1) or 1), 100)), data.get("created_by"), now, now))
        con.commit()
        return cur.lastrowid


def update_ai_provider(provider_id: int, fields: dict):
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = ts()
    # 只有設定（config）欄位變更才提升 config_version；狀態／檢查時間更新不算
    # Default selection changes orchestration only. It does not alter this
    # provider's model/transport capability, so it must not invalidate a
    # previously probed structured-output capability snapshot.
    config_fields = {"name", "provider_type", "base_url", "model", "fallback_model",
                     "secret_ciphertext", "enabled", "max_concurrency"}
    with WRITE_LOCK:
        con = _conn()
        if fields.get("is_default"):
            con.execute("UPDATE ai_providers SET is_default=0 WHERE deleted_at IS NULL AND id<>?", (provider_id,))
        if config_fields & set(fields):
            fields["config_version"] = (query_one("SELECT config_version FROM ai_providers WHERE id=?", (provider_id,)) or {}).get("config_version", 0) + 1
        sets = ", ".join(f"{key}=?" for key in fields)
        con.execute(f"UPDATE ai_providers SET {sets} WHERE id=?", (*fields.values(), provider_id))
        con.commit()


def delete_ai_provider(provider_id: int):
    now = ts()
    execute("UPDATE ai_providers SET deleted_at=?, updated_at=?, is_default=0 WHERE id=? AND deleted_at IS NULL", (now, now, provider_id))


def get_default_ai_provider():
    """嚴格回傳 is_default=1 且 enabled 的 provider；絕不做 first-row fallback。"""
    return query_one("SELECT * FROM ai_providers WHERE is_default=1 AND enabled=1 AND deleted_at IS NULL LIMIT 1")


# ---------- chapter_analyses（V4 canonical analysis repository） ----------

def create_chapter_analysis(data: dict) -> int:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO chapter_analyses(book_id, chapter_id, batch_job_id, source_text_hash, analysis_type, schema_version, "
            "analysis_profile, status, prompt_version, emotion_policy_version, ai_provider_id, ai_model, "
            "ai_config_version, created_by, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data["book_id"], data["chapter_id"], data.get("batch_job_id"),
             data["source_text_hash"], data.get("analysis_type", "speaker"),
             data.get("schema_version", 4),
             data.get("analysis_profile", "speaker"), data.get("status", "queued"),
             data.get("prompt_version", ""), data.get("emotion_policy_version"),
             data.get("ai_provider_id"), data.get("ai_model"), data.get("ai_config_version"),
             data.get("created_by"), now))
        con.commit()
        return cur.lastrowid


def get_chapter_analysis(analysis_id: int):
    return query_one("SELECT * FROM chapter_analyses WHERE id=?", (analysis_id,))


def get_analysis_partial(cache_key: str):
    return query_one("SELECT * FROM analysis_partials WHERE cache_key=?", (cache_key,))


def list_analysis_partials(*, analysis_id: int | None = None, chapter_id: int | None = None,
                           states: tuple[str, ...] | None = None, limit: int = 500):
    where, params = [], []
    if analysis_id is not None:
        where.append("analysis_id=?")
        params.append(analysis_id)
    if chapter_id is not None:
        where.append("chapter_id=?")
        params.append(chapter_id)
    if states:
        marks = ",".join("?" for _ in states)
        where.append(f"state IN ({marks})")
        params.extend(states)
    condition = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(max(1, min(int(limit), 5000)))
    return query(f"SELECT * FROM analysis_partials{condition} ORDER BY chunk_index, id LIMIT ?", tuple(params))


def create_analysis_partial(data: dict):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT OR IGNORE INTO analysis_partials("
            "cache_key, analysis_id, book_id, chapter_id, source_text_hash, chunk_index, "
            "chunk_input_hash, chunking_version, context_hash, prompt_hash, prompt_version, "
            "schema_version, analysis_profile, provider_identity, model_identity, "
            "provider_config_version, fallback_models_json, language, category, state, "
            "created_at, updated_at, last_used_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            , (data["cache_key"], data.get("analysis_id"), data["book_id"], data["chapter_id"],
               data["source_text_hash"], data["chunk_index"], data["chunk_input_hash"],
               data["chunking_version"], data["context_hash"], data["prompt_hash"],
               data["prompt_version"], data["schema_version"], data["analysis_profile"],
               data["provider_identity"], data["model_identity"], data.get("provider_config_version"),
               data.get("fallback_models_json", "[]"), data.get("language", ""),
               data.get("category", ""), data.get("state", "pending"), now, now, now))
        con.commit()
        return query_one("SELECT * FROM analysis_partials WHERE cache_key=?", (data["cache_key"],))


def claim_analysis_partial(cache_key: str, *, owner_token: str, lease_expires_at: str,
                           analysis_id: int | None = None) -> dict | None:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        row = con.execute("SELECT * FROM analysis_partials WHERE cache_key=?", (cache_key,)).fetchone()
        if row is None:
            return None
        if row["state"] == "succeeded":
            return dict(row)
        if row["state"] == "running" and row["lease_expires_at"] and row["lease_expires_at"] > now:
            return dict(row)
        cur = con.execute(
            "UPDATE analysis_partials SET state='running', owner_token=?, lease_expires_at=?, "
            "analysis_id=COALESCE(?, analysis_id), updated_at=? "
            "WHERE cache_key=? AND state IN ('pending','failed','stale') "
            "OR (cache_key=? AND state='running' AND (lease_expires_at IS NULL OR lease_expires_at<=?))",
            (owner_token, lease_expires_at, analysis_id, now, cache_key, cache_key, now),
        )
        con.commit()
        if cur.rowcount != 1:
            return query_one("SELECT * FROM analysis_partials WHERE cache_key=?", (cache_key,))
        return query_one("SELECT * FROM analysis_partials WHERE cache_key=?", (cache_key,))


def publish_analysis_partial(cache_key: str, *, owner_token: str, storage_path: str,
                             storage_sha256: str, source_text_hash: str) -> bool:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "UPDATE analysis_partials SET state='succeeded', storage_path=?, storage_sha256=?, "
            "owner_token=NULL, lease_expires_at=NULL, updated_at=?, last_used_at=? "
            "WHERE cache_key=? AND state='running' AND owner_token=? AND source_text_hash=? "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at>?",
            (storage_path, storage_sha256, now, now, cache_key, owner_token, source_text_hash, now),
        )
        con.commit()
        return cur.rowcount == 1


def mark_analysis_partial_failed(cache_key: str, *, owner_token: str, failure_code: str) -> bool:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "UPDATE analysis_partials SET state='failed', failure_code=?, owner_token=NULL, "
            "lease_expires_at=NULL, updated_at=? WHERE cache_key=? AND state='running' AND owner_token=?",
            (failure_code[:120], now, cache_key, owner_token),
        )
        con.commit()
        return cur.rowcount == 1


def mark_analysis_partial_stale(cache_key: str, failure_code: str = "stale") -> bool:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "UPDATE analysis_partials SET state='stale', failure_code=?, owner_token=NULL, "
            "lease_expires_at=NULL, updated_at=? WHERE cache_key=? AND state!='running'",
            (failure_code[:120], now, cache_key),
        )
        con.commit()
        return cur.rowcount == 1


def increment_analysis_partial_metrics(cache_key: str, *, request_count: int = 0,
                                       retry_count: int = 0, usage_metrics: dict | None = None):
    current = query_one("SELECT usage_metrics_json FROM analysis_partials WHERE cache_key=?", (cache_key,))
    try:
        aggregate = json.loads((current or {}).get("usage_metrics_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        aggregate = {}
    for key, value in (usage_metrics or {}).items():
        if key == "schemaVersion":
            aggregate[key] = value
        elif key == "usageAvailable":
            aggregate[key] = bool(aggregate.get(key) or value)
        else:
            try:
                aggregate[key] = max(0, int(aggregate.get(key) or 0)) + max(0, int(value or 0))
            except (TypeError, ValueError):
                pass
    execute(
        "UPDATE analysis_partials SET request_count=request_count+?, retry_count=retry_count+?, "
        "usage_metrics_json=?, updated_at=? WHERE cache_key=?",
        (max(0, int(request_count)), max(0, int(retry_count)),
         json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")), ts(), cache_key),
    )


def touch_analysis_partial(cache_key: str):
    execute("UPDATE analysis_partials SET last_used_at=?, updated_at=? WHERE cache_key=?",
            (ts(), ts(), cache_key))


def cleanup_analysis_partials(*, cutoff_succeeded: str, cutoff_other: str, limit: int = 100):
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        rows = con.execute(
            "SELECT p.id, p.cache_key, p.storage_path FROM analysis_partials p "
            "WHERE ((state='succeeded' AND COALESCE(last_used_at, updated_at) < ?) "
            "OR (state IN ('failed','stale') AND updated_at < ?)) "
            "AND NOT (p.state='running' AND p.lease_expires_at>? ) "
            "AND NOT EXISTS (SELECT 1 FROM chapter_analyses a "
            "WHERE a.id=p.analysis_id AND a.status IN ('queued','running')) "
            "ORDER BY p.updated_at LIMIT ?",
            (cutoff_succeeded, cutoff_other, now, max(1, min(int(limit), 100))),
        ).fetchall()
        if rows:
            marks = ",".join("?" for _ in rows)
            con.execute(
                f"UPDATE analysis_partials SET state='stale', storage_path=NULL, storage_sha256=NULL, "
                f"failure_code='retention_expired', owner_token=NULL, lease_expires_at=NULL, "
                f"updated_at=? WHERE id IN ({marks})",
                (now, *(r["id"] for r in rows)),
            )
            con.commit()
        return [dict(r) for r in rows]


def list_chapter_analyses(chapter_id: int):
    return query("SELECT * FROM chapter_analyses WHERE chapter_id=? ORDER BY id DESC", (chapter_id,))


def update_chapter_analysis(analysis_id: int, fields: dict):
    if not fields:
        return
    fields = dict(fields)
    sets = ", ".join(f"{key}=?" for key in fields)
    execute(f"UPDATE chapter_analyses SET {sets} WHERE id=?", (*fields.values(), analysis_id))


def mark_analysis_ready(analysis_id: int, artifact_path: str):
    update_chapter_analysis(analysis_id, {
        "status": "ready", "artifact_path": artifact_path,
        "finished_at": ts(), "error": "",
    })


def mark_analysis_ready_if_running(analysis_id: int, artifact_path: str,
                                  *, schema_version: int | None = None) -> bool:
    """CAS ready publication; cancellation cannot be overwritten by writer."""
    fields = {
        "status": "ready", "artifact_path": artifact_path,
        "finished_at": ts(), "error": "",
    }
    if schema_version is not None:
        fields["schema_version"] = int(schema_version)
    assignments = ", ".join(f"{key}=?" for key in fields)
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            f"UPDATE chapter_analyses SET {assignments} "
            "WHERE id=? AND status IN ('queued','running')",
            (*fields.values(), analysis_id),
        )
        con.commit()
        return cur.rowcount == 1


def mark_analysis_running_if_queued(analysis_id: int) -> bool:
    """Atomically enter running from queued; a terminal analysis is untouched."""
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "UPDATE chapter_analyses SET status='running' "
            "WHERE id=? AND status='queued'", (analysis_id,)
        )
        con.commit()
        return cur.rowcount == 1


def mark_analysis_failed(analysis_id: int, error: str):
    # Never slice a serialized JSON error: doing so can leave invalid JSON and
    # hide the machine-readable failure code.  Coverage diagnostics are already
    # bounded, but this final guard also protects other future error payloads.
    safe_error = error or ""
    try:
        parsed = json.loads(safe_error)
        if isinstance(parsed, dict):
            diagnostics = parsed.get("diagnostics")
            if isinstance(diagnostics, dict):
                for key in ("missingSegmentIds", "missingSegmentIdsSample",
                            "duplicateSegmentIds", "unknownSegmentIds"):
                    value = diagnostics.get(key)
                    if isinstance(value, list):
                        diagnostics[key] = value[:16]
                diagnostics["sampleTruncated"] = bool(
                    diagnostics.get("sampleTruncated") or
                    any(isinstance(diagnostics.get(key), list) and len(diagnostics[key]) >= 16
                        for key in ("missingSegmentIds", "missingSegmentIdsSample"))
                )
            if isinstance(parsed.get("message"), str):
                parsed["message"] = parsed["message"][:400]
            safe_error = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            if len(safe_error) > 2000 and isinstance(parsed, dict) and isinstance(parsed.get("diagnostics"), dict):
                diagnostics = parsed["diagnostics"]
                for key in ("missingSegmentIds", "missingSegmentIdsSample",
                            "duplicateSegmentIds", "unknownSegmentIds"):
                    diagnostics.pop(key, None)
                safe_error = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        if len(safe_error) > 2000:
            safe_error = json.dumps({
                "code": parsed.get("code", "analysis_failed") if isinstance(parsed, dict) else "analysis_failed",
                "failureType": parsed.get("failureType", "unknown") if isinstance(parsed, dict) else "unknown",
                "message": "分析失敗；詳細 diagnostics 已省略",
                "retryable": False,
            }, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, json.JSONDecodeError):
        safe_error = json.dumps({
            "code": "analysis_failed", "failureType": "unknown",
            "message": str(safe_error)[:400], "retryable": False,
        }, ensure_ascii=False, separators=(",", ":"))
    update_chapter_analysis(analysis_id, {
        "status": "failed", "error": safe_error, "finished_at": ts(),
    })


def repair_orphan_analysis_job(job_id: int, analysis_id: int, failure_json: str,
                               progress_json: str, *, require_exhausted: bool = True) -> bool:
    """Atomically close an exhausted running analysis job and its analysis row.

    This is deliberately narrower than requeue: it never resets attempts or
    makes the old job eligible for the worker again.  Startup orphan recovery
    may close a lost-owner job before its attempt budget is exhausted.
    """
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        job = con.execute(
            "SELECT status, attempts, max_attempts, analysis_id FROM generation_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        analysis = con.execute(
            "SELECT status, progress_json FROM chapter_analyses WHERE id=?", (analysis_id,)
        ).fetchone()
        if not job or not analysis or job["analysis_id"] != analysis_id \
                or job["status"] != "running" or analysis["status"] != "running" \
                or (require_exhausted and int(job["attempts"] or 0) < int(job["max_attempts"] or 3)):
            con.rollback()
            return False
        now = ts()
        con.execute(
            "UPDATE generation_jobs SET status='failed', progress=100, error=?, finished_at=? "
            "WHERE id=? AND status='running'",
            ((failure_json or "")[:2000], now, job_id),
        )
        con.execute(
            "UPDATE chapter_analyses SET status='failed', error=?, progress_json=?, finished_at=? "
            "WHERE id=? AND status='running'",
            ((failure_json or "")[:2000], progress_json or "{}", now, analysis_id),
        )
        con.commit()
        return True


def repair_orphan_analysis_batch_job(job_id: int, failure_json: str,
                                     analysis_failure_json: str,
                                     *, require_exhausted: bool = True) -> bool:
    """Atomically close a lost full-book analysis worker and active children.

    The batch master has no single ``analysis_id``.  Its child analyses are
    linked through ``batch_job_id`` and are converged together, while ready
    children remain historical results.  The old master is never requeued.
    """
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        job = con.execute(
            "SELECT status, attempts, max_attempts, job_type FROM generation_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if (not job or job["job_type"] != "speaker_analysis_all"
                or job["status"] != "running"
                or (require_exhausted and int(job["attempts"] or 0) < int(job["max_attempts"] or 3))):
            con.rollback()
            return False
        children = con.execute(
            "SELECT id, progress_json FROM chapter_analyses "
            "WHERE batch_job_id=? AND status IN ('queued','running') ORDER BY id",
            (job_id,),
        ).fetchall()
        now = ts()
        for child in children:
            try:
                progress = json.loads(child["progress_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                progress = {}
            if not isinstance(progress, dict):
                progress = {}
            progress.update({
                "currentStage": "failed",
                "runningChunks": 0,
                "lastError": "批次分析工作已中斷，請重新分析",
            })
            con.execute(
                "UPDATE chapter_analyses SET status='failed', error=?, progress_json=?, finished_at=? "
                "WHERE id=? AND batch_job_id=? AND status IN ('queued','running')",
                (analysis_failure_json, json.dumps(progress, ensure_ascii=False), now,
                 child["id"], job_id),
            )
        job_cur = con.execute(
            "UPDATE generation_jobs SET status='failed', progress=100, error=?, finished_at=? "
            "WHERE id=? AND job_type='speaker_analysis_all' AND status='running'",
            (failure_json, now, job_id),
        )
        if job_cur.rowcount != 1:
            con.rollback()
            return False
        con.commit()
        return True


def cancel_analysis_job(job_id: int, analysis_id: int, failure_json: str,
                        progress_json: str) -> bool:
    """Atomically cancel one queued/running analysis without requeueing it."""
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        job = con.execute(
            "SELECT status, analysis_id FROM generation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        analysis = con.execute(
            "SELECT status, progress_json FROM chapter_analyses WHERE id=?", (analysis_id,)
        ).fetchone()
        if (not job or not analysis or job["analysis_id"] != analysis_id
                or job["status"] not in ("pending", "running")
                or analysis["status"] not in ("queued", "running")):
            con.rollback()
            return False
        try:
            current_progress = json.loads(analysis["progress_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            current_progress = {}
        if not isinstance(current_progress, dict):
            current_progress = {}
        if not current_progress:
            try:
                current_progress = json.loads(progress_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                current_progress = {}
        if not isinstance(current_progress, dict):
            current_progress = {}
        current_progress.update({
            "currentStage": "failed",
            "runningChunks": 0,
            "lastError": "分析已取消",
        })
        persisted_progress = json.dumps(current_progress, ensure_ascii=False)
        now = ts()
        job_cur = con.execute(
            "UPDATE generation_jobs SET status='cancelled', progress=100, error=?, finished_at=? "
            "WHERE id=? AND analysis_id=? AND status IN ('pending','running')",
            (failure_json or "", now, job_id, analysis_id),
        )
        analysis_cur = con.execute(
            "UPDATE chapter_analyses SET status='failed', error=?, progress_json=?, finished_at=? "
            "WHERE id=? AND status IN ('queued','running')",
            (failure_json or "", persisted_progress, now, analysis_id),
        )
        if job_cur.rowcount != 1 or analysis_cur.rowcount != 1:
            con.rollback()
            return False
        con.commit()
        return True


def cancel_analysis_batch_job(job_id: int, job_failure_json: str,
                              analysis_failure_json: str) -> dict | None:
    """Atomically cancel a full-book analysis and its owned active chapters."""
    with WRITE_LOCK:
        con = _conn()
        con.execute("BEGIN IMMEDIATE")
        job = con.execute(
            "SELECT status, book_id, job_type FROM generation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if (not job or job["job_type"] != "speaker_analysis_all"
                or job["status"] not in ("pending", "running")):
            con.rollback()
            return None
        children = con.execute(
            "SELECT id, progress_json FROM chapter_analyses "
            "WHERE batch_job_id=? AND status IN ('queued','running') ORDER BY id",
            (job_id,),
        ).fetchall()
        now = ts()
        cancelled_analysis_ids = []
        for child in children:
            try:
                progress = json.loads(child["progress_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                progress = {}
            if not isinstance(progress, dict):
                progress = {}
            progress.update({
                "currentStage": "failed",
                "runningChunks": 0,
                "lastError": "批次分析已取消",
            })
            cur = con.execute(
                "UPDATE chapter_analyses SET status='failed', error=?, progress_json=?, finished_at=? "
                "WHERE id=? AND batch_job_id=? AND status IN ('queued','running')",
                (analysis_failure_json, json.dumps(progress, ensure_ascii=False), now,
                 child["id"], job_id),
            )
            if cur.rowcount == 1:
                cancelled_analysis_ids.append(child["id"])
        job_cur = con.execute(
            "UPDATE generation_jobs SET status='cancelled', progress=100, error=?, finished_at=? "
            "WHERE id=? AND job_type='speaker_analysis_all' AND status IN ('pending','running')",
            (job_failure_json, now, job_id),
        )
        if job_cur.rowcount != 1:
            con.rollback()
            return None
        con.commit()
        return {
            "jobId": job_id,
            "bookId": job["book_id"],
            "analysisIds": cancelled_analysis_ids,
            "cancelledAnalysisCount": len(cancelled_analysis_ids),
        }


def get_ready_chapter_analysis(chapter_id: int, source_text_hash: str):
    """回傳與章節目前文字 hash 相符的 ready analysis；不相符回 None。"""
    return query_one(
        "SELECT * FROM chapter_analyses WHERE chapter_id=? AND source_text_hash=? AND status='ready' "
        "ORDER BY id DESC LIMIT 1",
        (chapter_id, source_text_hash),
    )


def update_character_proposal(proposal_id: int, fields: dict):
    """更新 entity-resolution proposal 的非 canonical 狀態欄位。"""
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = ts()
    sets = ", ".join(f"{key}=?" for key in fields)
    execute(f"UPDATE character_resolution_proposals SET {sets} WHERE id=?", (*fields.values(), proposal_id))


def get_latest_chapter_analysis(chapter_id: int):
    """回傳章節最新一筆 analysis（任何 status）；無則 None。"""
    return query_one(
        "SELECT * FROM chapter_analyses WHERE chapter_id=? ORDER BY id DESC LIMIT 1",
        (chapter_id,),
    )


# ---------- audio_generations（V4 generation repository） ----------

def create_audio_generation(data: dict) -> int:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO audio_generations(book_id, chapter_id, mode, source_text_hash, analysis_id, character_registry_revision, "
            "voice_snapshot_json, tts_profile_snapshot_json, generation_key, status, "
            "tts_provider_id, provider_config_version, adapter_key, adapter_version, capabilities_hash, "
            "emotion_policy, emotion_schema_version, requested_by, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data["book_id"], data["chapter_id"], data["mode"], data["source_text_hash"],
             data.get("analysis_id"), data.get("character_registry_revision"), data.get("voice_snapshot_json", "{}"),
             data.get("tts_profile_snapshot_json", "{}"), data["generation_key"],
             data.get("status", "queued"), data.get("tts_provider_id"),
             data.get("provider_config_version"), data.get("adapter_key"), data.get("adapter_version"),
             data.get("capabilities_hash"), data.get("emotion_policy", "best_effort"),
             data.get("emotion_schema_version"), data.get("requested_by"), now))
        con.commit()
        return cur.lastrowid


def get_audio_generation(generation_id: int):
    return query_one("SELECT * FROM audio_generations WHERE id=?", (generation_id,))


def get_audio_generation_by_key(generation_key: str):
    return query_one("SELECT * FROM audio_generations WHERE generation_key=?", (generation_key,))


def list_audio_generations(chapter_id: int):
    return query("SELECT * FROM audio_generations WHERE chapter_id=? ORDER BY id DESC", (chapter_id,))


# ---------- expressive profiles ----------

def create_expressive_profile(data: dict) -> int:
    now = ts()
    with WRITE_LOCK:
        con = _conn()
        cur = con.execute(
            "INSERT INTO expressive_profiles(profile_id, book_id, speaker_id, voice_id, emotion, version, status, settings_json, availability_status, created_by, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data["profile_id"], data["book_id"], data["speaker_id"], data["voice_id"], data["emotion"],
             int(data.get("version", 1)), data.get("status", "active"), data.get("settings_json", "{}"),
             data.get("availability_status", "available"), data.get("created_by"), now, now),
        )
        con.commit()
        return cur.lastrowid


def list_expressive_profiles(book_id: int, speaker_id: str | None = None, emotion: str | None = None):
    clauses = ["book_id=?"]
    params = [book_id]
    if speaker_id:
        clauses.append("speaker_id=?")
        params.append(speaker_id)
    if emotion:
        clauses.append("emotion=?")
        params.append(emotion)
    return query("SELECT * FROM expressive_profiles WHERE " + " AND ".join(clauses) + " ORDER BY speaker_id, emotion, version DESC", params)


def get_expressive_profile(profile_id: str):
    return query_one("SELECT * FROM expressive_profiles WHERE profile_id=?", (profile_id,))


def update_expressive_profile(profile_id: str, fields: dict):
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = ts()
    sets = ", ".join(f"{key}=?" for key in fields)
    execute("UPDATE expressive_profiles SET " + sets + " WHERE profile_id=?", (*fields.values(), profile_id))


def update_audio_generation(generation_id: int, fields: dict):
    if not fields:
        return
    fields = dict(fields)
    sets = ", ".join(f"{key}=?" for key in fields)
    execute(f"UPDATE audio_generations SET {sets} WHERE id=?", (*fields.values(), generation_id))


def mark_generation_ready(generation_id: int, audio_path: str, timing_path: str):
    update_audio_generation(generation_id, {
        "status": "ready", "audio_path": audio_path, "timing_path": timing_path,
        "finished_at": ts(), "error": "",
    })


def mark_generation_failed(generation_id: int, error: str):
    update_audio_generation(generation_id, {
        "status": "failed", "error": (error or "")[:2000], "finished_at": ts(),
    })


def add_audit_log(actor_id: int | None, action: str, target_type: str = "", target_id: str = "",
                  details: dict = None, *, _con=None, _created_at: str | None = None,
                  correction_of_id: int | None = None):
    """所有 server-side canonical audit writes 的相容入口。

    `_con` 僅供已持有原子交易的資料層 helper 使用；一般呼叫會沿用
    `execute()` 的既有交易語意。詳細 payload 由 R18 audit service 統一
    驗證、遮蔽與限制大小。
    """
    from .services import audit as audit_service
    return audit_service.write_audit(
        actor_id, action, target_type, target_id, details,
        connection=_con, created_at=_created_at, correction_of_id=correction_of_id,
    )


def list_audit_logs(limit: int = 100):
    return query(
        "SELECT a.*, u.username FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    )


def list_audit_logs_paged(*, action: str = "", actor: str = "", date_from: str = "", date_to: str = "",
                          page: int = 1, page_size: int = 50):
    """稽核紀錄：動作/操作者搜尋 + 日期範圍 + server-side 分頁。不提供刪除（保留可追溯性）。"""
    where, params = [], []
    if action:
        where.append("a.action LIKE ?")
        params.append(f"%{action}%")
    if actor:
        where.append("u.username LIKE ? COLLATE NOCASE")
        params.append(f"%{actor}%")
    if date_from:
        where.append("date(a.created_at) >= ?")
        params.append(date_from[:10])
    if date_to:
        where.append("date(a.created_at) <= ?")
        params.append(date_to[:10])
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = query_one(f"SELECT COUNT(*) n FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id{cond}", tuple(params))["n"]
    per = max(1, min(int(page_size or 50), 200))
    pg = max(1, int(page))
    rows = query(
        f"SELECT a.*, u.username FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id{cond} "
        f"ORDER BY a.created_at DESC, a.id DESC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per))
    return {"items": rows, "total": total, "page": pg, "page_size": per,
            "total_pages": (total + per - 1) // per if total else 0}


def list_author_applications_paged(*, status: str = "pending", page: int = 1, page_size: int = 20):
    """作者申請：狀態篩選 + server-side 分頁。不刪除歷史（保留審核追蹤價值）。"""
    st = status if status in ("pending", "approved", "rejected") else "pending"
    total = query_one("SELECT COUNT(*) n FROM author_applications WHERE status=?", (st,))["n"]
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page))
    rows = query(
        "SELECT a.*, u.username FROM author_applications a JOIN users u ON u.id=a.user_id "
        "WHERE a.status=? ORDER BY a.created_at ASC LIMIT ? OFFSET ?",
        (st, per, (pg - 1) * per))
    return {"items": rows, "total": total, "page": pg, "page_size": per,
            "total_pages": (total + per - 1) // per if total else 0}
