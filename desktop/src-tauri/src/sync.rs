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
    pub status: String,   // "idle" | "syncing" | "error"
    pub message: Option<String>,
}

#[tauri::command]
pub async fn trigger_sync(app: AppHandle) -> Result<SyncStatus, String> {
    run_sync(&app).await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_sync_status(app: AppHandle) -> Result<SyncStatus, String> {
    // TODO: Read from local store
    Ok(SyncStatus {
        last_sync_at: None,
        status: "idle".to_string(),
        message: None,
    })
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

    // TODO:
    // 1. Get Conductor API key from credential store
    // 2. Fetch fresh QB Desktop data via Conductor
    // 3. Normalize and store in local DB
    // 4. POST updates to cloud backend API

    let now = chrono::Utc::now().to_rfc3339();
    let status = SyncStatus {
        last_sync_at: Some(now),
        status: "idle".to_string(),
        message: None,
    };

    let _ = app.emit("sync-completed", status.clone());
    Ok(status)
}
