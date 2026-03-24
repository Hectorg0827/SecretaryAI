/**
 * QB Desktop Onboarding Wizard
 *
 * Step flow:
 *  1. Check — detect whether QuickBooks Desktop is installed
 *  2. Connect — open Conductor's authFlowUrl in a webview
 *  3. Authorize — wait for the user to approve in QB, with AI visual guidance
 *  4. Done — confirm connection and navigate to main app
 */
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-shell";

// ── API base ──────────────────────────────────────────────────────────────────

const API =
  typeof window !== "undefined" && (window as any).__SECRETARY_API__
    ? (window as any).__SECRETARY_API__
    : "http://localhost:8000";

async function apiFetch(path: string, opts?: RequestInit) {
  const token = localStorage.getItem("secretary_token") ?? "";
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(opts?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────────────────────

type Step = "checking" | "not_found" | "connect" | "authorize" | "done" | "error";

interface StatusResp {
  connected: boolean;
  status: "connected" | "pending" | "error";
  message: string;
}

interface GuidanceResp {
  instruction: string;
  detected_state: string;
  needs_action: boolean;
}

// ── Main wizard ───────────────────────────────────────────────────────────────

export default function Setup() {
  const [step, setStep] = useState<Step>("checking");
  const [authFlowUrl, setAuthFlowUrl] = useState<string>("");
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [guidance, setGuidance] = useState<GuidanceResp | null>(null);
  const [error, setError] = useState<string>("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const guidanceRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Step 1: Detect QB on first render ──────────────────────────────────────
  useEffect(() => {
    detectQB();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (guidanceRef.current) clearInterval(guidanceRef.current);
    };
  }, []);

  async function detectQB() {
    setStep("checking");
    try {
      const installed: boolean = await invoke("check_qb_installed");
      if (installed) {
        await startSetup();
      } else {
        setStep("not_found");
      }
    } catch (e) {
      // If the Tauri command itself fails (e.g. Linux dev), assume installed and proceed
      console.warn("QB detection failed, assuming installed:", e);
      await startSetup();
    }
  }

  // ── Step 2: Create Conductor EndUser + get authFlowUrl ────────────────────
  async function startSetup() {
    try {
      const data = await apiFetch("/api/setup/qb-desktop/start", { method: "POST" });
      setAuthFlowUrl(data.auth_flow_url);
      setStep("connect");
    } catch (e: any) {
      setError(e.message ?? "Could not start QuickBooks setup.");
      setStep("error");
    }
  }

  // ── Step 3: User clicked "Open setup" — launch Conductor auth flow ─────────
  async function openAuthFlow() {
    if (!authFlowUrl) return;
    // Open in system browser so the .QWC file download works natively
    await open(authFlowUrl);
    setStep("authorize");
    startPolling();
    startGuidancePolling();
  }

  // ── Poll backend every 5 s for connection confirmation ────────────────────
  function startPolling() {
    pollRef.current = setInterval(async () => {
      try {
        const status: StatusResp = await apiFetch("/api/setup/qb-desktop/status");
        setStatusMessage(status.message);
        if (status.connected) {
          if (pollRef.current) clearInterval(pollRef.current);
          if (guidanceRef.current) clearInterval(guidanceRef.current);
          setStep("done");
        }
      } catch {
        // Silently ignore transient errors during polling
      }
    }, 5000);
  }

  // ── Poll every 15 s for AI visual guidance ────────────────────────────────
  function startGuidancePolling() {
    guidanceRef.current = setInterval(async () => {
      try {
        const screenshotB64: string = await invoke("capture_screen");
        const resp: GuidanceResp = await apiFetch("/api/setup/qb-desktop/guidance", {
          method: "POST",
          body: JSON.stringify({
            screenshot_b64: screenshotB64,
            step: "QuickBooks Web Connector authorization and password entry",
          }),
        });
        setGuidance(resp);
        if (!resp.needs_action) {
          if (guidanceRef.current) clearInterval(guidanceRef.current);
        }
      } catch {
        // Guidance is best-effort — don't show errors
      }
    }, 15000);
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <div style={styles.logo}>SecretaryAI</div>
        <h1 style={styles.title}>Connect QuickBooks Desktop</h1>

        {step === "checking" && (
          <WizardStep
            icon="🔍"
            heading="Checking your computer..."
            body="Looking for QuickBooks Desktop installation."
            showSpinner
          />
        )}

        {step === "not_found" && (
          <WizardStep
            icon="⚠️"
            heading="QuickBooks Desktop not found"
            body="SecretaryAI requires QuickBooks Desktop to be installed on this computer. Please install QuickBooks and re-launch the setup."
          >
            <button style={styles.btnSecondary} onClick={() => open("https://quickbooks.intuit.com/desktop/")}>
              Download QuickBooks
            </button>
            <button style={styles.btnPrimary} onClick={detectQB}>
              Try Again
            </button>
          </WizardStep>
        )}

        {step === "connect" && (
          <WizardStep
            icon="🔗"
            heading="Ready to connect"
            body="We'll open a secure setup page in your browser. It will guide you through downloading a small configuration file and adding it to QuickBooks — no technical knowledge needed."
          >
            <div style={styles.steps}>
              <StepBadge n={1} text="A browser window will open" />
              <StepBadge n={2} text="Download the .QWC file when prompted" />
              <StepBadge n={3} text="Open the file — QuickBooks will ask for permission" />
              <StepBadge n={4} text='Click "Yes, always allow access"' />
            </div>
            <button style={styles.btnPrimary} onClick={openAuthFlow}>
              Open Setup →
            </button>
          </WizardStep>
        )}

        {step === "authorize" && (
          <WizardStep
            icon="⏳"
            heading="Waiting for QuickBooks..."
            body={statusMessage || "Complete the steps in the browser window, then authorize access in QuickBooks when prompted."}
            showSpinner
          >
            {guidance && (
              <div style={styles.guidanceBox}>
                <div style={styles.guidanceLabel}>AI Assistant</div>
                <div style={styles.guidanceText}>{guidance.instruction}</div>
                {guidance.detected_state && (
                  <div style={styles.guidanceState}>I can see: {guidance.detected_state}</div>
                )}
              </div>
            )}
            <button style={styles.btnSecondary} onClick={openAuthFlow}>
              Re-open setup page
            </button>
          </WizardStep>
        )}

        {step === "done" && (
          <WizardStep
            icon="✅"
            heading="QuickBooks is connected!"
            body="SecretaryAI will now sync your QuickBooks data automatically in the background. You're all set."
          >
            <button style={styles.btnPrimary} onClick={() => window.location.replace("/")}>
              Go to Dashboard →
            </button>
          </WizardStep>
        )}

        {step === "error" && (
          <WizardStep
            icon="❌"
            heading="Something went wrong"
            body={error || "An unexpected error occurred. Please try again."}
          >
            <button style={styles.btnPrimary} onClick={detectQB}>
              Try Again
            </button>
          </WizardStep>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function WizardStep({
  icon,
  heading,
  body,
  showSpinner,
  children,
}: {
  icon: string;
  heading: string;
  body: string;
  showSpinner?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div style={styles.stepContainer}>
      <div style={styles.icon}>{icon}</div>
      <h2 style={styles.stepHeading}>{heading}</h2>
      <p style={styles.stepBody}>{body}</p>
      {showSpinner && <div style={styles.spinner} />}
      {children && <div style={styles.actions}>{children}</div>}
    </div>
  );
}

function StepBadge({ n, text }: { n: number; text: string }) {
  return (
    <div style={styles.stepBadge}>
      <span style={styles.stepNum}>{n}</span>
      <span style={styles.stepText}>{text}</span>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: "100vh",
    background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontFamily: "'Inter', system-ui, sans-serif",
    padding: "24px",
  },
  card: {
    background: "#fff",
    borderRadius: "16px",
    padding: "48px 40px",
    maxWidth: "480px",
    width: "100%",
    boxShadow: "0 25px 50px rgba(0,0,0,0.4)",
  },
  logo: {
    fontSize: "13px",
    fontWeight: 700,
    letterSpacing: "0.12em",
    textTransform: "uppercase" as const,
    color: "#6366f1",
    marginBottom: "8px",
  },
  title: {
    fontSize: "22px",
    fontWeight: 700,
    color: "#0f172a",
    margin: "0 0 32px",
  },
  stepContainer: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    textAlign: "center" as const,
    gap: "12px",
  },
  icon: { fontSize: "48px", lineHeight: 1 },
  stepHeading: {
    fontSize: "18px",
    fontWeight: 600,
    color: "#0f172a",
    margin: 0,
  },
  stepBody: {
    fontSize: "14px",
    color: "#64748b",
    lineHeight: 1.6,
    margin: "0 0 8px",
    maxWidth: "360px",
  },
  spinner: {
    width: "28px",
    height: "28px",
    border: "3px solid #e2e8f0",
    borderTop: "3px solid #6366f1",
    borderRadius: "50%",
    animation: "spin 1s linear infinite",
    margin: "8px auto",
  },
  actions: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "10px",
    width: "100%",
    marginTop: "8px",
  },
  btnPrimary: {
    background: "#6366f1",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "12px 24px",
    fontSize: "14px",
    fontWeight: 600,
    cursor: "pointer",
    width: "100%",
  },
  btnSecondary: {
    background: "transparent",
    color: "#6366f1",
    border: "1.5px solid #6366f1",
    borderRadius: "8px",
    padding: "11px 24px",
    fontSize: "14px",
    fontWeight: 600,
    cursor: "pointer",
    width: "100%",
  },
  steps: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "8px",
    width: "100%",
    textAlign: "left" as const,
    background: "#f8fafc",
    borderRadius: "10px",
    padding: "16px",
    marginTop: "4px",
  },
  stepBadge: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
  },
  stepNum: {
    background: "#6366f1",
    color: "#fff",
    borderRadius: "50%",
    width: "22px",
    height: "22px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "12px",
    fontWeight: 700,
    flexShrink: 0,
  },
  stepText: {
    fontSize: "13px",
    color: "#374151",
    lineHeight: 1.4,
  },
  guidanceBox: {
    background: "#eff6ff",
    border: "1.5px solid #bfdbfe",
    borderRadius: "10px",
    padding: "14px 16px",
    textAlign: "left" as const,
    width: "100%",
    marginTop: "4px",
  },
  guidanceLabel: {
    fontSize: "11px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "#3b82f6",
    marginBottom: "6px",
  },
  guidanceText: {
    fontSize: "14px",
    color: "#1e40af",
    lineHeight: 1.5,
    fontWeight: 500,
  },
  guidanceState: {
    fontSize: "12px",
    color: "#64748b",
    marginTop: "6px",
    fontStyle: "italic",
  },
};
