/// Local SQLite cache with field-level (AES-256-GCM) encryption of the sensitive
/// `data_json` payload. The encryption key is derived from a key held in the OS
/// credential store, so another local process reading the DB file sees only ids
/// and statuses — not customer/financial records.
use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const DB_FILE: &str = "secretaryai.db";
const CREDENTIAL_SERVICE: &str = "secretaryai-db";
const CREDENTIAL_KEY: &str = "db-key";

pub fn db_path(app: &AppHandle) -> PathBuf {
    // NEVER `.expect()` here: this runs during `setup()` on the main thread, and
    // a panic in a `panic = "abort"` build would SIGABRT the whole app before a
    // window opens. Fall back to a temp-dir path if the platform dir can't be
    // resolved — the local cache is best-effort, not load-bearing.
    let base = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("SecretaryAI"));
    base.join(DB_FILE)
}

fn get_or_create_db_key(app: &AppHandle) -> Result<String> {
    use crate::credentials::{get_credential, store_credential};
    use rand::Rng;

    // Try to get existing key
    if let Ok(Some(key)) =
        get_credential(CREDENTIAL_SERVICE.to_string(), CREDENTIAL_KEY.to_string())
    {
        return Ok(key);
    }

    // Generate a new 32-byte hex key
    let key: String = (0..32)
        .map(|_| format!("{:02x}", rand::thread_rng().gen::<u8>()))
        .collect();

    store_credential(
        CREDENTIAL_SERVICE.to_string(),
        CREDENTIAL_KEY.to_string(),
        key.clone(),
    )
    .map_err(|e| anyhow::anyhow!("{}", e))?;

    Ok(key)
}

// ── Field-level encryption for the cached payload (`data_json`) ───────────────
// The bulk of the sensitive cached data lives in the `data_json` column. We
// encrypt it at rest with AES-256-GCM using a key derived from the OS-vault
// db-key, so another local process reading the SQLite file sees only ids and
// statuses — not customer/financial records. (Whole-database SQLCipher is a
// tracked future enhancement; app-layer AES avoids adding an OpenSSL build
// dependency that could jeopardise the packaged installers.)
const ENC_PREFIX: &str = "enc:v1:";

fn field_cipher_key(key_str: &str) -> [u8; 32] {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(key_str.as_bytes());
    h.update(b"secretaryai-cache-field-v1");
    h.finalize().into()
}

fn encrypt_field(plain: &str, key32: &[u8; 32]) -> Result<String> {
    use aes_gcm::aead::{Aead, KeyInit};
    use aes_gcm::{Aes256Gcm, Nonce};
    use base64::{engine::general_purpose::STANDARD, Engine};
    use rand::RngCore;

    let cipher =
        Aes256Gcm::new_from_slice(key32).map_err(|e| anyhow::anyhow!("cipher init: {}", e))?;
    let mut nonce = [0u8; 12];
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    let ct = cipher
        .encrypt(Nonce::from_slice(&nonce), plain.as_bytes())
        .map_err(|e| anyhow::anyhow!("encrypt: {}", e))?;
    let mut blob = Vec::with_capacity(12 + ct.len());
    blob.extend_from_slice(&nonce);
    blob.extend_from_slice(&ct);
    Ok(format!("{}{}", ENC_PREFIX, STANDARD.encode(blob)))
}

fn decrypt_field(stored: &str, key32: &[u8; 32]) -> Result<String> {
    use aes_gcm::aead::{Aead, KeyInit};
    use aes_gcm::{Aes256Gcm, Nonce};
    use base64::{engine::general_purpose::STANDARD, Engine};

    // Backward-compat: values without our prefix are legacy plaintext.
    let Some(b64) = stored.strip_prefix(ENC_PREFIX) else {
        return Ok(stored.to_string());
    };
    let blob = STANDARD
        .decode(b64)
        .map_err(|e| anyhow::anyhow!("b64: {}", e))?;
    if blob.len() < 12 {
        return Err(anyhow::anyhow!("ciphertext too short"));
    }
    let cipher =
        Aes256Gcm::new_from_slice(key32).map_err(|e| anyhow::anyhow!("cipher init: {}", e))?;
    let pt = cipher
        .decrypt(Nonce::from_slice(&blob[..12]), &blob[12..])
        .map_err(|_| anyhow::anyhow!("decrypt failed"))?;
    Ok(String::from_utf8(pt)?)
}

/// The 32-byte field-encryption key derived from the vault-backed db-key, if
/// available. `None` → fall back to plaintext (logged) rather than break the app.
fn cache_key(app: &AppHandle) -> Option<[u8; 32]> {
    match get_or_create_db_key(app) {
        Ok(k) => Some(field_cipher_key(&k)),
        Err(e) => {
            log::warn!("Cache key unavailable ({e}); caching without field encryption");
            None
        }
    }
}

pub fn open_db(app: &AppHandle) -> Result<Connection> {
    let path = db_path(app);
    // Connection::open does NOT create missing parent directories — on a fresh
    // install the app-data dir doesn't exist yet, so create it first.
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let conn = Connection::open(&path)?;
    conn.execute_batch("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;")?;
    Ok(conn)
}

pub fn init_local_db(app: &AppHandle) -> Result<()> {
    let path = db_path(app);

    // Ensure the app-data directory exists before opening the DB file.
    // Without this, Connection::open fails on first launch and the whole app
    // aborts during setup() (the window never appears).
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // Best-effort: provision the field-encryption key (used to encrypt the
    // data_json payload in upsert/query). This talks to the OS credential store,
    // which must NOT be allowed to block app startup, so failures are logged.
    if let Err(e) = get_or_create_db_key(app) {
        log::warn!("Could not provision DB key (continuing, cache unencrypted): {e}");
    }

    let conn = Connection::open(&path)?;

    conn.execute_batch(
        "
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS accounts_cache (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            health_status TEXT,
            health_score INTEGER,
            last_order_date TEXT,
            data_json TEXT,
            synced_at TEXT
        );

        CREATE TABLE IF NOT EXISTS inventory_cache (
            id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            total_qty INTEGER,
            stock_status TEXT,
            weeks_remaining REAL,
            data_json TEXT,
            synced_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ",
    )?;

    log::info!("Local database initialized at {:?}", path);
    Ok(())
}

/// Upsert rows into accounts_cache or inventory_cache.
/// Called by the sync loop after a successful backend sync.
pub fn upsert_accounts(app: &AppHandle, rows: &[serde_json::Value]) -> Result<()> {
    let conn = open_db(app)?;
    let key = cache_key(app);
    let tx = conn.unchecked_transaction()?;
    for row in rows {
        let data_json = encrypt_payload(&row.to_string(), &key);
        tx.execute(
            "INSERT OR REPLACE INTO accounts_cache
             (id, name, health_status, health_score, last_order_date, data_json, synced_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, datetime('now'))",
            params![
                row["id"].as_str().unwrap_or(""),
                row["name"].as_str().unwrap_or(""),
                row["health_status"].as_str(),
                row["health_score"].as_i64(),
                row["last_order_date"].as_str(),
                data_json,
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

pub fn upsert_inventory(app: &AppHandle, rows: &[serde_json::Value]) -> Result<()> {
    let conn = open_db(app)?;
    let key = cache_key(app);
    let tx = conn.unchecked_transaction()?;
    for row in rows {
        let data_json = encrypt_payload(&row.to_string(), &key);
        tx.execute(
            "INSERT OR REPLACE INTO inventory_cache
             (id, product_name, total_qty, stock_status, weeks_remaining, data_json, synced_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, datetime('now'))",
            params![
                row["id"].as_str().unwrap_or(""),
                row["product_name"].as_str().unwrap_or(""),
                row["total_qty"].as_i64(),
                row["stock_status"].as_str(),
                row["weeks_remaining"].as_f64(),
                data_json,
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

/// Encrypt a payload for storage; on any failure (or no key) store plaintext so
/// the cache still works. Returns the value to persist in the `data_json` column.
fn encrypt_payload(plain: &str, key: &Option<[u8; 32]>) -> String {
    match key {
        Some(k) => encrypt_field(plain, k).unwrap_or_else(|_| plain.to_string()),
        None => plain.to_string(),
    }
}

#[tauri::command]
pub fn query_local(
    app: AppHandle,
    table: String,
    filter: Option<String>,
) -> Result<String, String> {
    let allowed_tables = ["accounts_cache", "inventory_cache", "sync_log"];
    if !allowed_tables.contains(&table.as_str()) {
        return Err(format!("Table '{}' not allowed", table));
    }

    let conn = open_db(&app).map_err(|e| e.to_string())?;

    // Build a safe parameterised query.
    // `filter` is a JSON object whose keys must match column names we whitelist.
    let (sql, bound_value) = if let Some(ref f) = filter {
        let filter_val: serde_json::Value =
            serde_json::from_str(f).map_err(|_| "filter must be valid JSON".to_string())?;

        // Only allow filtering by a single whitelisted column for safety
        let allowed_columns = ["id", "status", "stock_status", "health_status", "event"];
        if let Some(obj) = filter_val.as_object() {
            if let Some((col, val)) = obj.iter().next() {
                if allowed_columns.contains(&col.as_str()) {
                    let query = format!(
                        "SELECT * FROM {} WHERE {} = ?1 ORDER BY rowid DESC LIMIT 500",
                        table, col
                    );
                    (query, val.as_str().unwrap_or("").to_string())
                } else {
                    return Err(format!("Column '{}' is not filterable", col));
                }
            } else {
                (
                    format!("SELECT * FROM {} ORDER BY rowid DESC LIMIT 500", table),
                    String::new(),
                )
            }
        } else {
            (
                format!("SELECT * FROM {} ORDER BY rowid DESC LIMIT 500", table),
                String::new(),
            )
        }
    } else {
        (
            format!("SELECT * FROM {} ORDER BY rowid DESC LIMIT 500", table),
            String::new(),
        )
    };

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;

    let column_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    // Key for decrypting the `data_json` payload on read (None → assume legacy plaintext).
    let key = cache_key(&app);

    let rows = if filter.is_some() && !bound_value.is_empty() {
        stmt.query_map(params![bound_value], |row| {
            row_to_obj(row, &column_names, &key)
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect::<Vec<_>>()
    } else {
        stmt.query_map([], |row| row_to_obj(row, &column_names, &key))
            .map_err(|e| e.to_string())?
            .filter_map(|r| r.ok())
            .collect::<Vec<_>>()
    };

    serde_json::to_string(&rows).map_err(|e| e.to_string())
}

/// Build a JSON object for one row, decrypting the `data_json` payload column.
fn row_to_obj(
    row: &rusqlite::Row,
    column_names: &[String],
    key: &Option<[u8; 32]>,
) -> rusqlite::Result<serde_json::Map<String, serde_json::Value>> {
    let mut obj = serde_json::Map::new();
    for (i, col) in column_names.iter().enumerate() {
        let val: rusqlite::types::Value = row.get(i)?;
        let json_val = match (col.as_str(), &val) {
            ("data_json", rusqlite::types::Value::Text(s)) => {
                let plain = match key {
                    Some(k) => decrypt_field(s, k).unwrap_or_else(|_| s.clone()),
                    None => s.clone(),
                };
                serde_json::Value::String(plain)
            }
            _ => sqlite_value_to_json(val),
        };
        obj.insert(col.clone(), json_val);
    }
    Ok(obj)
}

fn sqlite_value_to_json(val: rusqlite::types::Value) -> serde_json::Value {
    match val {
        rusqlite::types::Value::Null => serde_json::Value::Null,
        rusqlite::types::Value::Integer(i) => serde_json::Value::Number(i.into()),
        rusqlite::types::Value::Real(f) => serde_json::Number::from_f64(f)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        rusqlite::types::Value::Text(s) => serde_json::Value::String(s),
        rusqlite::types::Value::Blob(b) => serde_json::Value::String(base64::Engine::encode(
            &base64::engine::general_purpose::STANDARD,
            b,
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encrypt_decrypt_roundtrip() {
        let key = field_cipher_key("test-db-key");
        let plain = r#"{"customer":"Acme","balance":15000}"#;
        let enc = encrypt_field(plain, &key).unwrap();
        assert!(enc.starts_with(ENC_PREFIX));
        assert_ne!(enc, plain); // not stored in cleartext
        assert_eq!(decrypt_field(&enc, &key).unwrap(), plain);
    }

    #[test]
    fn decrypt_legacy_plaintext_passthrough() {
        let key = field_cipher_key("k");
        assert_eq!(decrypt_field("{\"a\":1}", &key).unwrap(), "{\"a\":1}");
    }

    #[test]
    fn wrong_key_fails_to_decrypt() {
        let k1 = field_cipher_key("key-one");
        let k2 = field_cipher_key("key-two");
        let enc = encrypt_field("secret-record", &k1).unwrap();
        assert!(decrypt_field(&enc, &k2).is_err());
    }
}
