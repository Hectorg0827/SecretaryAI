/// Screen capture module for Computer Use.
/// Captures the primary display and returns a base64-encoded PNG.
/// Only captures when an active Computer Use session is running
/// and the user has granted screen capture permission.
use anyhow::{anyhow, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::AppHandle;

/// Global flag: true while a Computer Use task is actively running.
/// Screen capture is only allowed while this flag is true.
static COMPUTER_USE_ACTIVE: AtomicBool = AtomicBool::new(false);

/// Activate Computer Use mode (called when a CU task starts).
/// Returns false if the user has not granted screen capture permission.
pub fn activate_computer_use() -> bool {
    // Check permission by attempting a silent test capture
    if _capture_and_encode().is_ok() {
        COMPUTER_USE_ACTIVE.store(true, Ordering::SeqCst);
        true
    } else {
        false
    }
}

/// Deactivate Computer Use mode (called when a CU task ends).
pub fn deactivate_computer_use() {
    COMPUTER_USE_ACTIVE.store(false, Ordering::SeqCst);
}

/// Capture the primary display and return as base64 PNG.
/// Returns an error if Computer Use is not currently active.
#[tauri::command]
pub fn capture_screen() -> Result<String, String> {
    if !COMPUTER_USE_ACTIVE.load(Ordering::SeqCst) {
        return Err(
            "Screen capture is only allowed during an active Computer Use session".to_string(),
        );
    }
    _capture_and_encode().map_err(|e| e.to_string())
}

fn _capture_and_encode() -> Result<String> {
    #[cfg(target_os = "windows")]
    {
        capture_windows()
    }
    #[cfg(target_os = "macos")]
    {
        capture_macos()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        Err(anyhow!("Screen capture not supported on this platform"))
    }
}

// ─── Windows capture ──────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
fn capture_windows() -> Result<String> {
    use std::process::Command;

    // Use PowerShell to capture the screen as PNG bytes.
    // This is the simplest cross-version approach on Windows without extra deps.
    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            r#"
Add-Type -AssemblyName System.Windows.Forms;
Add-Type -AssemblyName System.Drawing;
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height);
$graphics = [System.Drawing.Graphics]::FromImage($bitmap);
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size);
$ms = New-Object System.IO.MemoryStream;
$bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);
[Convert]::ToBase64String($ms.ToArray())
"#,
        ])
        .output()
        .map_err(|e| anyhow!("PowerShell screen capture failed: {}", e))?;

    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(anyhow!("Screen capture error: {}", err));
    }

    let b64 = String::from_utf8(output.stdout)
        .map_err(|e| anyhow!("UTF-8 decode error: {}", e))?
        .trim()
        .to_string();

    Ok(b64)
}

// ─── macOS capture ─────────────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
fn capture_macos() -> Result<String> {
    use std::fs;
    use std::process::Command;

    // screencapture writes to a temp file, then we read + encode it
    let tmp_path = "/tmp/secretaryai_capture.png";

    let status = Command::new("screencapture")
        .args(["-x", "-m", tmp_path]) // -x: no sound, -m: main display only
        .status()
        .map_err(|e| anyhow!("screencapture failed: {}", e))?;

    if !status.success() {
        return Err(anyhow!("screencapture exited with status: {}", status));
    }

    let bytes = fs::read(tmp_path).map_err(|e| anyhow!("Failed to read capture file: {}", e))?;

    let _ = fs::remove_file(tmp_path); // clean up

    Ok(STANDARD.encode(&bytes))
}
