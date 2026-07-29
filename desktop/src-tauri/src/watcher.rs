use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
/// File system watcher — monitors a designated folder for depletion reports
/// (PDFs and Excel files) dropped by sub-distributors.
/// Cross-platform: uses the `notify` crate which works on Windows and macOS.
use std::path::Path;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::sync::mpsc;

const WATCH_FOLDER_KEY: &str = "watch_folder";

pub async fn start_file_watcher(app: AppHandle) {
    let watch_path = get_watch_folder(&app);

    // Create the directory if it doesn't exist yet (first launch)
    if let Err(e) = std::fs::create_dir_all(&watch_path) {
        log::warn!("Could not create watch folder {}: {}", watch_path, e);
    }

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

                    // Upload the file to the cloud backend for processing
                    let path_clone = path.clone();
                    let app_clone = app.clone();
                    tokio::spawn(async move {
                        if let Err(e) = upload_report(&app_clone, &path_clone).await {
                            log::error!("Failed to upload report {:?}: {}", path_clone, e);
                            let _ = app_clone.emit(
                                "report-upload-error",
                                serde_json::json!({
                                    "path": path_clone.to_string_lossy(),
                                    "error": e.to_string(),
                                }),
                            );
                        }
                    });
                }
            }
        }
    }
}

fn get_watch_folder(_app: &AppHandle) -> String {
    // Check env override first (useful for testing)
    if let Ok(path) = std::env::var("SECRETARY_WATCH_FOLDER") {
        return path;
    }
    // Default to user's Documents/SecretaryAI/Reports
    let documents = dirs_next::document_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    documents
        .join("SecretaryAI")
        .join("Reports")
        .to_string_lossy()
        .to_string()
}

async fn upload_report(app: &AppHandle, path: &std::path::Path) -> anyhow::Result<()> {
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
        log::warn!("Skipping report upload: no auth token stored");
        return Ok(());
    };

    let file_bytes = tokio::fs::read(path)
        .await
        .map_err(|e| anyhow::anyhow!("could not read file: {}", e))?;

    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("report")
        .to_string();

    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("bin")
        .to_lowercase();

    let mime = match ext.as_str() {
        "pdf" => "application/pdf",
        "xlsx" => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls" => "application/vnd.ms-excel",
        "csv" => "text/csv",
        _ => "application/octet-stream",
    };

    let part = reqwest::multipart::Part::bytes(file_bytes)
        .file_name(file_name.clone())
        .mime_str(mime)?;
    let form = reqwest::multipart::Form::new().part("file", part);

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()?;

    let response = client
        .post(format!("{}/api/agent/upload-report", api_url))
        .bearer_auth(&auth_token)
        .multipart(form)
        .send()
        .await
        .map_err(|e| anyhow::anyhow!("upload request failed: {}", e))?;

    if !response.status().is_success() {
        let status_code = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(anyhow::anyhow!("upload failed ({}): {}", status_code, body));
    }

    log::info!("Report uploaded successfully: {}", file_name);
    let _ = app.emit(
        "report-upload-complete",
        serde_json::json!({ "file_name": file_name }),
    );
    Ok(())
}
