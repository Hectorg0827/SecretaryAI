# Cross-Platform Support Guide

SecretaryAI's desktop app supports both **Windows** and **macOS**.

## Platform Differences

### Credential Storage
Credentials (API keys, OAuth tokens) are stored in the OS-native secure vault:

| Platform | Storage |
|----------|---------|
| Windows  | Windows Credential Manager (accessed via `windows` crate) |
| macOS    | Keychain Services (accessed via `security-framework` crate) |

Code: `desktop/src-tauri/src/credentials.rs`

The Rust code compiles conditionally per platform using `#[cfg(target_os = "windows")]` / `#[cfg(target_os = "macos")]`.

### App Distribution

| Platform | Format | Notes |
|----------|--------|-------|
| Windows  | `.msi` (Wix) or `.exe` (NSIS) | Code-signed with certificate |
| macOS    | `.dmg` containing `.app` | Notarized by Apple for Gatekeeper |

### System Tray
- **Windows**: Appears in the system tray (bottom-right taskbar)
- **macOS**: Appears in the menu bar (top-right)

Both use the same Tauri tray API — no platform-specific code needed.

### Minimize to Tray (Close Button Behavior)
- **Windows**: Clicking × hides the window (stays in system tray)
- **macOS**: Standard macOS behavior — window hides, app stays in Dock + menu bar

See `desktop/src-tauri/src/lib.rs` `on_window_event` handler.

### Auto-start on Login
- **Windows**: Adds a registry entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- **macOS**: Creates a Launch Agent plist in `~/Library/LaunchAgents/`

Handled by `tauri-plugin-autostart` automatically.

### File Watch Folder Default
- **Windows**: `C:\Users\<user>\Documents\SecretaryAI\Reports\`
- **macOS**: `/Users/<user>/Documents/SecretaryAI/Reports/`

## Building for Each Platform

You must build on the target platform (cross-compilation is complex for Tauri).

### Windows (build on Windows)
```bash
cd desktop
npm run tauri:build:windows
# Output: src-tauri/target/release/bundle/msi/*.msi
#         src-tauri/target/release/bundle/nsis/*.exe
```

### macOS Intel (build on macOS Intel or Apple Silicon with Rosetta)
```bash
cd desktop
rustup target add x86_64-apple-darwin
npm run tauri:build:macos
# Output: src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/*.dmg
```

### macOS Apple Silicon (build on Apple Silicon Mac)
```bash
cd desktop
rustup target add aarch64-apple-darwin
npm run tauri:build:macos-arm
# Output: src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
```

### Universal macOS Binary (fat binary)
```bash
cd desktop
rustup target add x86_64-apple-darwin aarch64-apple-darwin
tauri build --target universal-apple-darwin
```

## GitHub Actions (CI/CD)

For automated builds on each platform, configure separate GitHub Actions runners:
- `windows-latest` for Windows builds
- `macos-latest` for macOS builds (use `macos-13` for Intel, `macos-14` for Apple Silicon)
