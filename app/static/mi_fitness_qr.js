// Shared Mi Fitness QR-login polling logic, used by both the onboarding
// "connect" page and the Connections page. Both pages call
// startMiFitnessLogin(containerId[, successUrl]) against the same two
// backend routes: POST /api/data-sources/mi-fitness/connect and
// GET /api/data-sources/mi-fitness/status.
//
// successUrl is optional: on /connections the page itself IS the status
// display, so a reload is enough; on /onboarding/connect there is no
// forward progress from a reload (it just re-renders the same "Step 4 of
// 4" form), so that call site passes "/dashboard" to navigate onward like
// every other provider's connect flow does.
function startMiFitnessLogin(containerId, successUrl) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const startBtn = container.querySelector("[data-mi-fitness-start]");
  const statusEl = container.querySelector("[data-mi-fitness-status]");
  const qrEl = container.querySelector("[data-mi-fitness-qr]");

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    statusEl.textContent = "Starting login...";

    const startResponse = await fetch("/api/data-sources/mi-fitness/connect", { method: "POST" });
    if (!startResponse.ok) {
      statusEl.textContent = "Failed to start login. Please try again.";
      startBtn.disabled = false;
      return;
    }

    pollMiFitnessStatus(statusEl, qrEl, startBtn, successUrl);
  });
}

async function pollMiFitnessStatus(statusEl, qrEl, startBtn, successUrl) {
  const showError = (message) => {
    statusEl.textContent = message;
    qrEl.style.display = "none";
    startBtn.disabled = false;
  };

  const poll = async () => {
    try {
      const response = await fetch("/api/data-sources/mi-fitness/status");
      const data = await response.json();

      if (data.status === "qr_ready" || data.status === "starting") {
        if (data.qr_image_url) {
          qrEl.src = data.qr_image_url;
          qrEl.style.display = "block";
          statusEl.textContent = "Scan this QR code with the Mi Fitness app";
        } else {
          statusEl.textContent = "Preparing QR code...";
        }
        setTimeout(poll, 2000);
      } else if (data.status === "success") {
        if (successUrl) {
          statusEl.textContent = "Connected! Redirecting...";
          window.location.href = successUrl;
        } else {
          statusEl.textContent = "Connected! Reloading...";
          window.location.reload();
        }
      } else if (data.status === "error") {
        showError(`Connection failed: ${data.error}`);
      } else {
        setTimeout(poll, 2000);
      }
    } catch (err) {
      // Most commonly: the admin session expired mid-scan (the QR login
      // window can be open up to 5 minutes) and the authenticated status
      // fetch got redirected to the login page, so response.json() threw
      // on the returned HTML. Without this catch the poll loop just dies
      // here silently -- the button stays disabled and the status text
      // stays frozen forever with no indication anything went wrong.
      showError("Connection lost -- please refresh and try again");
    }
  };

  poll();
}
