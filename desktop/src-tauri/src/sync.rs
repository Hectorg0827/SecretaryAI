/// Background sync loop — pushes local QB data to the cloud backend.
/// Runs every 5 minutes while the app is active; immediately on user request.
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::time::sleep;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SyncStatus {
    pub last_sync_at: Option<String>,
    pub status: String, // "idle" | "syncing" | "error"
    pub message: Option<String>,
}

#[tauri::command]
pub async fn trigger_sync(app: AppHandle) -> Result<SyncStatus, String> {
    run_sync(&app).await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_sync_status(app: AppHandle) -> Result<SyncStatus, String> {
    // Read last sync status from the local DB sync_log table
    match read_last_sync_status(&app) {
        Ok(status) => Ok(status),
        Err(_) => Ok(SyncStatus {
            last_sync_at: None,
            status: "idle".to_string(),
            message: None,
        }),
    }
}

pub async fn start_sync_loop(app: AppHandle) {
    log::info!("Starting background sync loop");
    loop {
        sleep(Duration::from_secs(5 * 60)).await; // every 5 minutes
        if let Err(e) = run_sync(&app).await {
            log::error!("Sync error: {}", e);
            let _ = app.emit(
                "sync-error",
                SyncStatus {
                    last_sync_at: None,
                    status: "error".to_string(),
                    message: Some(e.to_string()),
                },
            );
        }
    }
}

async fn run_sync(app: &AppHandle) -> Result<SyncStatus> {
    log::info!("Running sync...");

    let _ = app.emit(
        "sync-started",
        SyncStatus {
            last_sync_at: None,
            status: "syncing".to_string(),
            message: Some("Syncing with QuickBooks...".to_string()),
        },
    );

    // 1. Get auth token and API URL from credential / env store
    let api_url = std::env::var("SECRETARY_API_URL").unwrap_or_else(|_| {
        if cfg!(debug_assertions) {
            "http://localhost:8000".to_string()
        } else {
            "https://api.secretaryai.com".to_string()
        }
    });

    let token =
        crate::credentials::get_credential("secretary-auth".to_string(), "token".to_string())
            .map_err(|e| anyhow::anyhow!("credential read error: {}", e))?;

    let Some(auth_token) = token else {
        // Not authenticated yet — skip sync silently
        log::info!("Sync skipped: no auth token stored");
        let status = SyncStatus {
            last_sync_at: None,
            status: "idle".to_string(),
            message: Some("Not authenticated".to_string()),
        };
        let _ = app.emit("sync-completed", status.clone());
        return Ok(status);
    };

    // 2. POST /api/sync/trigger — backend fetches QB data from Conductor
    //    and returns a summary of what changed
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()?;

    let response = client
        .post(format!("{}/api/agent/sync", api_url))
        .bearer_auth(&auth_token)
        .json(&serde_json::json!({"source": "desktop_agent"}))
        .send()
        .await
        .map_err(|e| anyhow::anyhow!("sync request failed: {}", e))?;

    if !response.status().is_success() {
        let status_code = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(anyhow::anyhow!("sync failed ({}): {}", status_code, body));
    }

    let payload: serde_json::Value = response.json().await.unwrap_or_default();
    let summary = payload["message"]
        .as_str()
        .unwrap_or("Sync complete")
        .to_string();

    log::info!("Sync complete: {}", summary);

    // 3. Write sync result to local DB log
    let now = chrono::Utc::now().to_rfc3339();
    if let Err(e) = write_sync_log(app, "sync_complete", "success", &summary) {
        log::warn!("Could not write sync log: {}", e);
    }

    let status = SyncStatus {
        last_sync_at: Some(now),
        status: "idle".to_string(),
        message: None,
    };

    let _ = app.emit("sync-completed", status.clone());
    Ok(status)
}

// ── Local DB helpers ──────────────────────────────────────────────────────────

fn db_path(app: &AppHandle) -> std::path::PathBuf {
    // Delegate to the canonical, panic-free resolver in `db` so the path (and
    // its temp-dir fallback) stays consistent and never aborts the process.
    crate::db::db_path(app)
}

fn write_sync_log(app: &AppHandle, event: &str, status: &str, details: &str) -> Result<()> {
    let conn = rusqlite::Connection::open(db_path(app))?;
    conn.execute(
        "INSERT INTO sync_log (event, status, details) VALUES (?1, ?2, ?3)",
        rusqlite::params![event, status, details],
    )?;
    Ok(())
}

fn read_last_sync_status(app: &AppHandle) -> Result<SyncStatus> {
    let conn = rusqlite::Connection::open(db_path(app))?;
    let mut stmt =
        conn.prepare("SELECT created_at, status, details FROM sync_log ORDER BY id DESC LIMIT 1")?;
    let row = stmt.query_row([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, Option<String>>(2)?,
        ))
    });
    match row {
        Ok((created_at, status, details)) => Ok(SyncStatus {
            last_sync_at: Some(created_at),
            status: if status == "success" {
                "idle".to_string()
            } else {
                "error".to_string()
            },
            message: details,
        }),
        Err(_) => Ok(SyncStatus {
            last_sync_at: None,
            status: "idle".to_string(),
            message: None,
        }),
    }
}
