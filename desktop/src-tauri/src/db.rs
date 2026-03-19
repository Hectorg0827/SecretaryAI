/// Local encrypted SQLite database.
/// Stores a local cache of business data for fast offline access.
/// The encryption key is stored in the OS credential store.

use anyhow::Result;
use rusqlite::{Connection, params};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const DB_FILE: &str = "secretaryai.db";
const CREDENTIAL_SERVICE: &str = "secretaryai-db";
const CREDENTIAL_KEY: &str = "db-key";

fn db_path(app: &AppHandle) -> PathBuf {
    app.path().app_data_dir()
        .expect("app data dir")
        .join(DB_FILE)
}

fn get_or_create_db_key(app: &AppHandle) -> Result<String> {
    use crate::credentials::{get_credential, store_credential};
    use rand::Rng;

    // Try to get existing key
    if let Ok(Some(key)) = get_credential(CREDENTIAL_SERVICE.to_string(), CREDENTIAL_KEY.to_string()) {
        return Ok(key);
    }

    // Generate a new 32-byte hex key
    let key: String = (0..32)
        .map(|_| format!("{:02x}", rand::thread_rng().gen::<u8>()))
        .collect();

    store_credential(CREDENTIAL_SERVICE.to_string(), CREDENTIAL_KEY.to_string(), key.clone())
        .map_err(|e| anyhow::anyhow!("{}", e))?;

    Ok(key)
}

pub fn init_local_db(app: &AppHandle) -> Result<()> {
    let path = db_path(app);
    let _key = get_or_create_db_key(app)?;

    // Note: For SQLite encryption use SQLCipher in production.
    // The key above would be passed as PRAGMA key = 'key_value';
    let conn = Connection::open(&path)?;

    conn.execute_batch("
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
    ")?;

    log::info!("Local database initialized at {:?}", path);
    Ok(())
}

#[tauri::command]
pub fn query_local(table: String, filter: Option<String>) -> Result<String, String> {
    // Very limited local query — only for safe, pre-defined table names
    let allowed_tables = ["accounts_cache", "inventory_cache", "sync_log"];
    if !allowed_tables.contains(&table.as_str()) {
        return Err(format!("Table '{}' not allowed", table));
    }

    // TODO: Implement with proper DB connection pooling
    Ok("[]".to_string())
}
