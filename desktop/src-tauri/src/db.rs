/// Local encrypted SQLite database.
/// Stores a local cache of business data for fast offline access.
/// The encryption key is stored in the OS credential store.
use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const DB_FILE: &str = "secretaryai.db";
const CREDENTIAL_SERVICE: &str = "secretaryai-db";
const CREDENTIAL_KEY: &str = "db-key";

pub fn db_path(app: &AppHandle) -> PathBuf {
    app.path()
        .app_data_dir()
        .expect("app data dir")
        .join(DB_FILE)
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

    // Best-effort: provision a key for future at-rest encryption. This talks to
    // the OS credential store, which must NOT be allowed to block app startup,
    // so failures are logged and ignored (the key is not used yet).
    if let Err(e) = get_or_create_db_key(app) {
        log::warn!("Could not provision DB key (continuing without it): {e}");
    }

    // Note: For SQLite encryption use SQLCipher in production.
    // The key above would be passed as PRAGMA key = 'key_value';
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
    let tx = conn.unchecked_transaction()?;
    for row in rows {
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
                row.to_string(),
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
}

pub fn upsert_inventory(app: &AppHandle, rows: &[serde_json::Value]) -> Result<()> {
    let conn = open_db(app)?;
    let tx = conn.unchecked_transaction()?;
    for row in rows {
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
                row.to_string(),
            ],
        )?;
    }
    tx.commit()?;
    Ok(())
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

    let rows = if filter.is_some() && !bound_value.is_empty() {
        stmt.query_map(params![bound_value], |row| {
            let mut obj = serde_json::Map::new();
            for (i, col) in column_names.iter().enumerate() {
                let val: rusqlite::types::Value = row.get(i)?;
                obj.insert(col.clone(), sqlite_value_to_json(val));
            }
            Ok(obj)
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect::<Vec<_>>()
    } else {
        stmt.query_map([], |row| {
            let mut obj = serde_json::Map::new();
            for (i, col) in column_names.iter().enumerate() {
                let val: rusqlite::types::Value = row.get(i)?;
                obj.insert(col.clone(), sqlite_value_to_json(val));
            }
            Ok(obj)
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect::<Vec<_>>()
    };

    serde_json::to_string(&rows).map_err(|e| e.to_string())
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
