/// Cross-platform credential storage.
/// - Windows: Windows Credential Manager (DPAPI)
/// - macOS: Keychain Services
/// Credentials are NEVER stored in plain text files.

use anyhow::Result;

/// Store a credential securely in the OS credential store.
#[tauri::command]
pub fn store_credential(service: String, key: String, value: String) -> Result<(), String> {
    store_impl(&service, &key, &value).map_err(|e| e.to_string())
}

/// Retrieve a credential from the OS credential store.
#[tauri::command]
pub fn get_credential(service: String, key: String) -> Result<Option<String>, String> {
    get_impl(&service, &key).map_err(|e| e.to_string())
}

/// Delete a credential from the OS credential store.
#[tauri::command]
pub fn delete_credential(service: String, key: String) -> Result<(), String> {
    delete_impl(&service, &key).map_err(|e| e.to_string())
}

// ─── Windows implementation ──────────────────────────────────────────────────

#[cfg(target_os = "windows")]
mod platform {
    use anyhow::{anyhow, Result};
    use std::ffi::OsStr;
    use std::iter::once;
    use std::os::windows::ffi::OsStrExt;

    fn to_wide(s: &str) -> Vec<u16> {
        OsStr::new(s).encode_wide().chain(once(0)).collect()
    }

    pub fn store(service: &str, key: &str, value: &str) -> Result<()> {
        use windows::Win32::Security::Credentials::*;
        use windows::Win32::Foundation::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        let blob = value.as_bytes();

        let cred = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: windows::core::PWSTR(target.as_ptr() as *mut _),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_ptr() as *mut u8,
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            ..Default::default()
        };

        unsafe {
            CredWriteW(&cred, 0).map_err(|e| anyhow!("CredWrite failed: {}", e))?;
        }
        Ok(())
    }

    pub fn get(service: &str, key: &str) -> Result<Option<String>> {
        use windows::Win32::Security::Credentials::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        let mut cred_ptr = std::ptr::null_mut();

        unsafe {
            match CredReadW(
                windows::core::PWSTR(target.as_ptr() as *mut _),
                CRED_TYPE_GENERIC,
                0,
                &mut cred_ptr,
            ) {
                Ok(_) => {
                    let cred = &*cred_ptr;
                    let blob = std::slice::from_raw_parts(
                        cred.CredentialBlob,
                        cred.CredentialBlobSize as usize,
                    );
                    let value = String::from_utf8(blob.to_vec())
                        .map_err(|e| anyhow!("UTF-8 error: {}", e))?;
                    CredFree(cred_ptr as *mut _);
                    Ok(Some(value))
                }
                Err(_) => Ok(None),
            }
        }
    }

    pub fn delete(service: &str, key: &str) -> Result<()> {
        use windows::Win32::Security::Credentials::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        unsafe {
            let _ = CredDeleteW(
                windows::core::PWSTR(target.as_ptr() as *mut _),
                CRED_TYPE_GENERIC,
                0,
            );
        }
        Ok(())
    }
}

// ─── macOS implementation ─────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
mod platform {
    use anyhow::{anyhow, Result};
    use security_framework::passwords::{delete_generic_password, get_generic_password, set_generic_password};

    pub fn store(service: &str, key: &str, value: &str) -> Result<()> {
        set_generic_password(service, key, value.as_bytes())
            .map_err(|e| anyhow!("Keychain store error: {}", e))
    }

    pub fn get(service: &str, key: &str) -> Result<Option<String>> {
        match get_generic_password(service, key) {
            Ok(bytes) => Ok(Some(
                String::from_utf8(bytes).map_err(|e| anyhow!("UTF-8 error: {}", e))?,
            )),
            Err(_) => Ok(None),
        }
    }

    pub fn delete(service: &str, key: &str) -> Result<()> {
        let _ = delete_generic_password(service, key);
        Ok(())
    }
}

// ─── Linux implementation (AES-256-GCM encrypted file) ───────────────────────
//
// Each credential is stored as an individual file under
//   ~/.local/share/secretaryai/credentials/<service>/<key>.enc
//
// The file format is: [12-byte nonce][ciphertext]
// The encryption key is derived from the machine ID (or a fallback) using SHA-256.
// This is not as strong as a hardware-backed store but is far better than plaintext.

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
mod platform {
    use aes_gcm::{
        aead::{Aead, KeyInit},
        Aes256Gcm, Nonce,
    };
    use anyhow::{anyhow, Result};
    use rand::RngCore;
    use sha2::{Digest, Sha256};
    use std::{fs, path::PathBuf};

    fn cred_dir() -> PathBuf {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home)
            .join(".local")
            .join("share")
            .join("secretaryai")
            .join("credentials")
    }

    fn cred_path(service: &str, key: &str) -> PathBuf {
        // Sanitize: replace any path separators
        let safe_service = service.replace(['/', '\\', '.'], "_");
        let safe_key     = key.replace(['/', '\\', '.'], "_");
        cred_dir().join(safe_service).join(format!("{}.enc", safe_key))
    }

    /// Derive a 32-byte encryption key from the machine-id (or fallback secret).
    fn derive_key() -> [u8; 32] {
        // Try /etc/machine-id first (stable across reboots on systemd systems)
        let seed = fs::read_to_string("/etc/machine-id")
            .or_else(|_| fs::read_to_string("/var/lib/dbus/machine-id"))
            .unwrap_or_else(|_| "secretaryai-linux-fallback-key".to_string());

        let mut hasher = Sha256::new();
        hasher.update(seed.trim().as_bytes());
        hasher.update(b"secretaryai-credential-store-v1");
        hasher.finalize().into()
    }

    pub fn store(service: &str, key: &str, value: &str) -> Result<()> {
        let path = cred_path(service, key);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        let enc_key = derive_key();
        let cipher  = Aes256Gcm::new_from_slice(&enc_key)
            .map_err(|e| anyhow!("cipher init: {}", e))?;

        let mut nonce_bytes = [0u8; 12];
        rand::rngs::OsRng.fill_bytes(&mut nonce_bytes);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let ciphertext = cipher
            .encrypt(nonce, value.as_bytes())
            .map_err(|e| anyhow!("encrypt: {}", e))?;

        // Write: 12-byte nonce + ciphertext
        let mut blob = Vec::with_capacity(12 + ciphertext.len());
        blob.extend_from_slice(&nonce_bytes);
        blob.extend_from_slice(&ciphertext);
        fs::write(&path, blob)?;
        Ok(())
    }

    pub fn get(service: &str, key: &str) -> Result<Option<String>> {
        let path = cred_path(service, key);
        if !path.exists() {
            return Ok(None);
        }

        let blob = fs::read(&path)?;
        if blob.len() < 12 {
            return Err(anyhow!("credential file too short"));
        }

        let enc_key = derive_key();
        let cipher  = Aes256Gcm::new_from_slice(&enc_key)
            .map_err(|e| anyhow!("cipher init: {}", e))?;

        let nonce      = Nonce::from_slice(&blob[..12]);
        let ciphertext = &blob[12..];

        let plaintext = cipher
            .decrypt(nonce, ciphertext)
            .map_err(|_| anyhow!("decryption failed — key mismatch or corrupted file"))?;

        Ok(Some(String::from_utf8(plaintext)?))
    }

    pub fn delete(service: &str, key: &str) -> Result<()> {
        let path = cred_path(service, key);
        if path.exists() {
            fs::remove_file(path)?;
        }
        Ok(())
    }
}

// ─── Delegating functions ─────────────────────────────────────────────────────

fn store_impl(service: &str, key: &str, value: &str) -> Result<()> {
    platform::store(service, key, value)
}

fn get_impl(service: &str, key: &str) -> Result<Option<String>> {
    platform::get(service, key)
}

fn delete_impl(service: &str, key: &str) -> Result<()> {
    platform::delete(service, key)
}
