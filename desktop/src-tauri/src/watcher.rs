/// File system watcher — monitors a designated folder for depletion reports
/// (PDFs and Excel files) dropped by sub-distributors.
/// Cross-platform: uses the `notify` crate which works on Windows and macOS.

use std::path::Path;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use tokio::sync::mpsc;

const WATCH_FOLDER_KEY: &str = "watch_folder";

pub async fn start_file_watcher(app: AppHandle) {
    // TODO: Load watch folder path from settings store
    let watch_path = get_watch_folder(&app);

    if !Path::new(&watch_path).exists() {
        log::warn!("Watch folder does not exist: {}", watch_path);
        return;
    }

    log::info!("Watching folder: {}", watch_path);

    let (tx, mut rx) = mpsc::channel(32);

    let mut watcher = RecommendedWatcher::new(
        move |result: Result<Event, notify::Error>| {
            if let Ok(event) = result {
                let _ = tx.blocking_send(event);
            }
        },
        Config::default().with_poll_interval(Duration::from_secs(10)),
    )
    .expect("failed to create file watcher");

    watcher
        .watch(Path::new(&watch_path), RecursiveMode::NonRecursive)
        .expect("failed to start watching folder");

    while let Some(event) = rx.recv().await {
        if matches!(event.kind, EventKind::Create(_) | EventKind::Modify(_)) {
            for path in &event.paths {
                let ext = path
                    .extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("")
                    .to_lowercase();

                if matches!(ext.as_str(), "xlsx" | "xls" | "csv" | "pdf") {
                    log::info!("New report file detected: {:?}", path);
                    let _ = app.emit(
                        "new-report-file",
                        serde_json::json!({
                            "path": path.to_string_lossy(),
                            "extension": ext,
                        }),
                    );
                    // TODO: Upload file to cloud for processing
                }
            }
        }
    }
}

fn get_watch_folder(app: &AppHandle) -> String {
    // Default to user's Documents/SecretaryAI/Reports
    let documents = dirs_next::document_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    documents
        .join("SecretaryAI")
        .join("Reports")
        .to_string_lossy()
        .to_string()
}
