use chrono::Utc;
use log::{info, warn};
use std::time::Duration;
use tokio::time::sleep;

const HEARTBEAT_INTERVAL_SECS: u64 = 300; // 5 minutes
const MAX_CONSECUTIVE_FAILURES: u32 = 3;

#[derive(serde::Serialize)]
struct HeartbeatPayload<'a> {
    company_id: &'a str,
    agent_version: &'a str,
    platform: &'a str,
    timestamp: String,
}

pub async fn start_heartbeat(api_base_url: String, company_id: String, agent_version: String) {
    let platform = std::env::consts::OS;
    let client = reqwest::Client::new();
    let url = format!("{}/api/agent/heartbeat", api_base_url.trim_end_matches('/'));
    let mut failures: u32 = 0;

    info!("Heartbeat: starting for company {} → {}", company_id, url);

    loop {
        let payload = HeartbeatPayload {
            company_id: &company_id,
            agent_version: &agent_version,
            platform,
            timestamp: Utc::now().to_rfc3339(),
        };

        match client
            .post(&url)
            .json(&payload)
            .timeout(Duration::from_secs(10))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                if failures > 0 {
                    info!("Heartbeat: recovered after {} failures", failures);
                }
                failures = 0;
            }
            Ok(resp) => {
                failures += 1;
                warn!(
                    "Heartbeat: server returned {} (failure {}/{})",
                    resp.status(),
                    failures,
                    MAX_CONSECUTIVE_FAILURES
                );
            }
            Err(err) => {
                failures += 1;
                warn!(
                    "Heartbeat: request failed — {} (failure {}/{})",
                    err, failures, MAX_CONSECUTIVE_FAILURES
                );
            }
        }

        if failures >= MAX_CONSECUTIVE_FAILURES {
            warn!(
                "Heartbeat: {} consecutive failures — agent still running but cloud unreachable",
                failures
            );
            // Reset so the warning repeats every MAX_CONSECUTIVE_FAILURES intervals
            failures = 0;
        }

        sleep(Duration::from_secs(HEARTBEAT_INTERVAL_SECS)).await;
    }
}
